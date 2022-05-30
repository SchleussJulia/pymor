from pymor.core.config import config, is_jupyter


if config.HAVE_DUNEGDT:
    import numpy as np
    from functools import partial
    from numbers import Number

    from dune.xt.grid import (
            ApplyOnBoundaryIntersections,
            ApplyOnCustomBoundaryIntersections,
            Dim,
            DirichletBoundary,
            NeumannBoundary,
            RobinBoundary,
            Walker,
            )
    from dune.xt.functions import divergence, GridFunction as GF
    from dune.xt.la import Istl
    from dune.gdt import (
            BilinearForm,
            ContinuousLagrangeSpace,
            DirichletConstraints,
            DiscreteFunction,
            LocalElementIntegralBilinearForm,
            LocalElementIntegralFunctional,
            LocalElementProductIntegrand,
            LocalIntersectionIntegralBilinearForm,
            LocalIntersectionIntegralFunctional,
            LocalIntersectionNormalComponentProductIntegrand,
            LocalIntersectionProductIntegrand,
            LocalLaplaceIntegrand,
            LocalLinearAdvectionIntegrand,
            MatrixOperator,
            VectorFunctional,
            boundary_interpolation,
            default_interpolation,
            make_element_sparsity_pattern,
            )

    from pymor.algorithms.preassemble import preassemble as preassemble_
    from pymor.algorithms.timestepping import ExplicitEulerTimeStepper, ImplicitEulerTimeStepper
    from pymor.analyticalproblems.elliptic import StationaryProblem
    from pymor.analyticalproblems.instationary import InstationaryProblem
    from pymor.bindings.dunegdt import DuneXTMatrixOperator
    from pymor.core.base import ImmutableObject
    from pymor.discretizers.dunegdt.gui import (
            DuneGDT1dAsNumpyVisualizer, DuneGDTK3dVisualizer, DuneGDTParaviewVisualizer)
    from pymor.discretizers.dunegdt.functions import DuneGridFunction
    from pymor.discretizers.dunegdt.problems import InstationaryDuneProblem, StationaryDuneProblem
    from pymor.models.basic import InstationaryModel, StationaryModel
    from pymor.operators.constructions import ConstantOperator, LincombOperator, VectorArrayOperator
    from pymor.tools.floatcmp import float_cmp


    def discretize_stationary_cg(analytical_problem, diameter=None, domain_discretizer=None,
                                 grid_type=None, grid=None, boundary_info=None,
                                 order=1, data_approximation_order=2, la_backend=Istl(),
                                 advection_in_divergence_form=True, preassemble=True, mu_energy_product=None):
        """Discretizes a |StationaryProblem| with dune-gdt using continuous Lagrange finite
           elements.

        Note: data functions might be replaced by their respective interpolations.

        Parameters
        ----------
        analytical_problem
            The |StationaryProblem| to discretize.
        diameter
            If not `None`, `diameter` is passed as an argument to the `domain_discretizer`.
        domain_discretizer
            Discretizer to be used for discretizing the analytical domain. This has
            to be a function `domain_discretizer(domain_description, diameter, ...)`.
            If `None`, :meth:`pymor.discretizers.dunegdt.domaindiscretizers.default.discretize_domain_default`
            is used.
        grid_type
            If not `None`, this parameter (has to be `simplex` or `cube`) is forwarded to
            `domain_discretizer` to specify the type of the generated |Grid|.
        grid
            Instead of using a domain discretizer, the |Grid| can also be passed directly using this
            parameter.
        boundary_info
            A |BoundaryInfo| specifying the boundary types of the grid boundary entities. Must be
            provided if `grid` is specified.
        order
            Order of the Finite Element space.
        data_approximation_order
            Polynomial order (on each grid element) for the interpolation of the data functions.
        la_backend
            Tag to determine which linear algebra backend from dune-xt is used.
        advection_in_divergence_form
            If true, treats linear advection as advertised in StationaryProblem (i.e.
            :math:`∇ ⋅ (v u)`), else as in :math:`v ⋅∇ u` (where :math:`v` denotes the vector
            field).
        preassemble
            If `True`, preassemble all operators in the resulting |Model|.
        mu_energy_product
            If specified, converted to a |Mu| and used to assemble an appropriate energy product.

        Returns
        -------
        m
            The |Model| that has been generated.
        data
            Dictionary with the following entries:

                :grid:                  The generated grid from dune.xt.grid.
                :boundary_info:         The generated boundary info from dune.xt.grid.
                :space:                 The generated approximation space from dune.gdt.
                :interpolate:           To interpolate data functions in the solution space.
                :unassembled_m:         In case `preassemble` is `True`, the generated |Model|
                                        before preassembling operators.
        """

        # convert problem, creates grid, boundary info and checks and converts all data functions
        assert isinstance(analytical_problem, StationaryProblem)
        p = StationaryDuneProblem.from_pymor(
                analytical_problem,
                data_approximation_order=data_approximation_order,
                diameter=diameter, domain_discretizer=domain_discretizer,
                grid_type=grid_type, grid=grid, boundary_info=boundary_info)

        return _discretize_stationary_cg_dune(p, order=order, la_backend=la_backend,
                advection_in_divergence_form=advection_in_divergence_form, preassemble=preassemble,
                mu_energy_product=mu_energy_product)


    def _discretize_stationary_cg_dune(dune_problem, order=1, la_backend=Istl(), advection_in_divergence_form=True,
            preassemble=True, mu_energy_product=None):
        """Discretizes a |StationaryDuneProblem| with dune-gdt using continuous Lagrange finite
           elements.

           Note: usually not to be used directly, see :meth:`discretize_stationary_cg` instead.
        """
        assert isinstance(dune_problem, StationaryDuneProblem)
        p = dune_problem
        grid, boundary_info = p.grid, p.boundary_info
        d = grid.dimension

        # some preparations
        space = ContinuousLagrangeSpace(grid, order=order, dim_range=Dim(1))
        sparsity_pattern = make_element_sparsity_pattern(space)
        constrained_lhs_ops = []
        constrained_lhs_coeffs = []
        unconstrained_lhs_ops = []
        unconstrained_lhs_coeffs = []
        if mu_energy_product is not None:
            mu_energy_product = p.parameters.parse(mu_energy_product)
            energy_product_ops = []
            energy_product_coeffs = []
        contrained_rhs_ops = []
        constrained_rhs_coeffs = []
        unconstrained_rhs_ops = []
        unconstrained_rhs_coeffs = []

        # diffusion part
        def make_diffusion_operator(func):
            bf = BilinearForm(grid)
            bf += LocalElementIntegralBilinearForm(LocalLaplaceIntegrand(GF(grid, func, (Dim(d), Dim(d)))))
            op = MatrixOperator(grid, space, space, la_backend, sparsity_pattern)
            op.append(bf)
            return op

        if p.diffusion:
            constrained_lhs_ops += [make_diffusion_operator(func) for func in p.diffusion.functions]
            constrained_lhs_coeffs += list(p.diffusion.coefficients)
            if mu_energy_product:
                energy_product_ops += [make_diffusion_operator(func) for func in p.diffusion.functions]
                energy_product_coeffs += list(p.diffusion.coefficients)

        # reaction part
        def make_weighted_l2_operator(func):
            bf = BilinearForm(grid)
            bf += LocalElementIntegralBilinearForm(LocalElementProductIntegrand(GF(grid, func)))
            op = MatrixOperator(grid, space, space, la_backend, sparsity_pattern)
            op.append(bf)
            return op

        if p.reaction:
            constrained_lhs_ops += [make_weighted_l2_operator(func) for func in p.reaction.functions]
            constrained_lhs_coeffs += list(p.reaction.coefficients)
            if mu_energy_product:
                energy_product_ops += [make_weighted_l2_operator(func) for func in p.reaction.functions]
                energy_product_coeffs += list(p.reaction.coefficients)

        # advection part
        if p.advection:
            def make_advection_operator(func):
                bf = BilinearForm(grid)
                bf += LocalElementIntegralBilinearForm(LocalLinearAdvectionIntegrand(
                    GF(grid, func), advection_in_divergence_form))

                if p.diffusion and advection_in_divergence_form: # to ensure Neumann boundary values
                    bf += (LocalIntersectionIntegralBilinearForm(
                             LocalIntersectionNormalComponentProductIntegrand(GF(grid, func))), {},
                           ApplyOnCustomBoundaryIntersections(grid, boundary_info, NeumannBoundary()))
                op = MatrixOperator(grid, space, space, la_backend, sparsity_pattern)
                op.append(bf)
                return op

            constrained_lhs_ops += [make_advection_operator(func) for func in p.advection.functions]
            constrained_lhs_coeffs += list(p.advection.coefficients)

            if mu_energy_product:
                energy_product_ops += [make_weighted_l2_operator(divergence(func)) for func in p.advection.functions]
                energy_product_coeffs += [-0.5*coeff for coeff in p.advection.coefficients]

        # robin boundaries
        if p.robin_data:
            assert isinstance(p.robin_data, tuple) and len(p.robin_data) == 2
            robin_parameter, robin_boundary_values = p.robin_data

            # contributions to the left hand side
            def make_weighted_l2_robin_boundary_operator(func):
                bf = BilinearForm(grid)
                bf += (LocalIntersectionIntegralBilinearForm(LocalIntersectionProductIntegrand(GF(grid, func))), {},
                       ApplyOnCustomBoundaryIntersections(grid, boundary_info, RobinBoundary()))
                op = MatrixOperator(grid, space, space, la_backend, sparsity_pattern)
                op.append(bf)
                return op

            unconstrained_lhs_ops += [make_weighted_l2_robin_boundary_operator(func)
                                      for func in robin_parameter.functions]
            unconstrained_lhs_coeffs += list(robin_parameter.coefficients)

            # contributions to the right hand side
            def make_weighted_l2_robin_boundary_functional(r_param_func, r_bv_func):
                op = VectorFunctional(grid, space, la_backend)
                op += (LocalIntersectionIntegralFunctional(
                            LocalIntersectionProductIntegrand(GF(grid, r_param_func)).with_ansatz(r_bv_func)), {},
                       ApplyOnCustomBoundaryIntersections(grid, boundary_info, RobinBoundary()))
                return op

            for r_param_func, r_param_coeff in zip(robin_parameter.functions, robin_parameter.coefficients):
                for r_bv_func, r_bv_coeff in zip(robin_boundary_values.functions, robin_boundary_values.coefficients):
                    contrained_rhs_ops += [make_weighted_l2_robin_boundary_functional(r_param_func, r_bv_func)]
                    constrained_rhs_coeffs += [r_param_coeff*r_bv_coeff]

        # source contribution
        if p.rhs:
            def make_l2_functional(func):
                op = VectorFunctional(grid, space, la_backend)
                op += LocalElementIntegralFunctional(
                        LocalElementProductIntegrand(GF(grid, 1)).with_ansatz(GF(grid, func)))
                return op

            contrained_rhs_ops += [make_l2_functional(func) for func in p.rhs.functions]
            constrained_rhs_coeffs += list(p.rhs.coefficients)

        # Neumann boundaries
        if p.neumann_data:
            def make_l2_neumann_boundary_functional(func):
                op = VectorFunctional(grid, space, la_backend)
                op += (LocalIntersectionIntegralFunctional(
                            LocalIntersectionProductIntegrand(GF(grid, -1)).with_ansatz(GF(grid, func))), {},
                       ApplyOnCustomBoundaryIntersections(grid, boundary_info, NeumannBoundary()))
                return op

            contrained_rhs_ops += [make_l2_neumann_boundary_functional(func) for func in p.neumann_data.functions]
            constrained_rhs_coeffs += list(p.neumann_data.coefficients)

        # Dirichlet boundaries are handled further below ...

        # products
        l2_product = make_weighted_l2_operator(1)
        h1_semi_product = make_diffusion_operator(1)

        # output functionals
        outputs = []
        if p.outputs:
            if any(v[0] not in ('l2', 'l2_boundary') for v in p.outputs):
                raise NotImplementedError(f'I do not know how to discretize a {v[0]} output!')
            for output_type, output_data in p.outputs:
                assert isinstance(output_data, DuneGridFunction)  # as in: not LincombDuneGridFunction
                output_data = output_data.impl
                if output_type == 'l2':
                    op = VectorFunctional(grid, space, la_backend)
                    op += LocalElementIntegralFunctional(LocalElementProductIntegrand(grid).with_ansatz(output_data))
                    outputs.append(op)
                elif output_type == 'l2_boundary':
                    op = VectorFunctional(grid, space, la_backend)
                    op += (LocalIntersectionIntegralFunctional(
                            LocalIntersectionProductIntegrand(GF(grid, 1)).with_ansatz(GF(grid, output_data))), {},
                            ApplyOnBoundaryIntersections(grid))
                    outputs.append(op)
                else:
                    raise NotImplementedError(f'I do not know how to discretize a {v[0]} output!')

        # assemble all of the above in one grid walk
        walker = Walker(grid)
        dirichlet_constraints = DirichletConstraints(boundary_info, space)
        walker.append(dirichlet_constraints)
        for op in constrained_lhs_ops:
            walker.append(op)
        for op in unconstrained_lhs_ops:
            walker.append(op)
        for op in contrained_rhs_ops:
            walker.append(op)
        walker.append(l2_product)
        walker.append(h1_semi_product)
        if mu_energy_product:
            for op in energy_product_ops:
                walker.append(op)
        for op in outputs:
            walker.append(op)
        walker.walk(thread_parallel=False)  # support not stable/enabled yet

        # Dirichlet boundaries
        if p.dirichlet_data:
            def make_dirichlet_interpolation(func):
                # first, we restrict the data to the Dirichlet boundary
                dirichlet_data = boundary_interpolation(
                        GF(grid, func),
                        space if order == 1 \
                                else ContinuousLagrangeSpace(grid, order=1), boundary_info, DirichletBoundary())
                # second, we only do something if dirichlet_data != 0
                if not float_cmp(dirichlet_data.dofs.vector.sup_norm(), 0.):
                    # third, we embed them in the solution space
                    dirichlet_data = default_interpolation(GF(grid, dirichlet_data), space)
                return dirichlet_data.dofs.vector
            unconstrained_rhs_ops += [make_dirichlet_interpolation(func) for func in p.dirichlet_data.functions]
            unconstrained_rhs_coeffs += list(p.dirichlet_data.coefficients)

        # extract vectors from functionals
        contrained_rhs_ops = [op.vector for op in contrained_rhs_ops]

        # prepare additional products
        # - in H^1
        if mu_energy_product:
            energy_product = MatrixOperator(
                    grid, space, space,
                    matrix=LincombOperator(
                        operators=[DuneXTMatrixOperator(op.matrix.copy()) for op in energy_product_ops],
                        coefficients=energy_product_coeffs).assemble(mu=mu_energy_product).matrix)
        # - in H^1_0
        l2_0_product = MatrixOperator(grid, space, space, l2_product.matrix.copy())     # using operators here just for
        h1_0_semi_product = MatrixOperator(grid, space, space, h1_semi_product.matrix.copy())  # unified handling below
        if mu_energy_product:
            energy_product_0 = MatrixOperator(grid, space, space, energy_product.matrix.copy())

        # apply the Dirichlet constraints
        for op in constrained_lhs_ops:
            dirichlet_constraints.apply(op.matrix, only_clear=True, ensure_symmetry=False)
        for vec in contrained_rhs_ops:
            dirichlet_constraints.apply(vec) # sets to zero
        dirichlet_constraints.apply(l2_0_product.matrix, ensure_symmetry=True)
        dirichlet_constraints.apply(h1_0_semi_product.matrix, ensure_symmetry=True)
        if mu_energy_product:
            dirichlet_constraints.apply(energy_product_0.matrix, ensure_symmetry=True)

        # create a matrix to hold the unit rows/cols corresponding to Dirichlet DoFs
        op = MatrixOperator(grid, space, space, la_backend, sparsity_pattern)
        dirichlet_constraints.apply(op.matrix)
        lhs_ops = [op] + constrained_lhs_ops + unconstrained_lhs_ops
        lhs_coeffs = [1.] + constrained_lhs_coeffs + unconstrained_lhs_coeffs

        # wrap everything as pyMOR operators:
        # - lhs
        lhs_ops = [DuneXTMatrixOperator(op.matrix) for op in lhs_ops]
        L = LincombOperator(operators=lhs_ops, coefficients=lhs_coeffs, name='ellipticOperator')

        # - rhs, clean up beforehand
        rhs_ops = [VectorArrayOperator(lhs_ops[0].range.make_array([vec,])) for vec in unconstrained_rhs_ops]
        rhs_coeffs = list(unconstrained_rhs_coeffs)
        for vec, coeff in zip(contrained_rhs_ops, constrained_rhs_coeffs):
            if not float_cmp(vec.sup_norm(), 0.):
                rhs_ops += [VectorArrayOperator(lhs_ops[0].range.make_array([vec,])),]
                rhs_coeffs += [coeff,]
        if len(rhs_ops) > 0:
            F = LincombOperator(operators=rhs_ops, coefficients=rhs_coeffs, name='rhsOperator')
        else:
            F = VectorArrayOperator(L.range.zeros(1))
        del contrained_rhs_ops, constrained_rhs_coeffs, unconstrained_rhs_ops, unconstrained_rhs_coeffs

        # - products
        products = {}
        #   * in H^1
        products.update({
            'l2': DuneXTMatrixOperator(l2_product.matrix, name='l2'),
            'h1_semi': DuneXTMatrixOperator(h1_semi_product.matrix, name='h1_semi'),
            'h1': (DuneXTMatrixOperator(l2_product.matrix)
                   + DuneXTMatrixOperator(h1_semi_product.matrix)).assemble().with_(name='h1'),
        })
        if mu_energy_product:
            products['energy'] = DuneXTMatrixOperator(energy_product.matrix, name='energy')
        #   * in H^1_0
        products.update({
            'l2_0': DuneXTMatrixOperator(l2_0_product.matrix, name='l2_0'),
            'h1_0_semi': DuneXTMatrixOperator(h1_0_semi_product.matrix, name='h1_0_semi'),
            'h1_0': (DuneXTMatrixOperator(l2_0_product.matrix)
                     + DuneXTMatrixOperator(h1_0_semi_product.matrix)).assemble().with_(name='h1_0'),
        })
        if mu_energy_product:
            products['energy_0'] = DuneXTMatrixOperator(energy_product_0.matrix, name='energy_0')

        # - outputs
        outputs = [VectorArrayOperator(lhs_ops[0].source.make_array([op.vector,]), adjoint=True)
                   for op in outputs]

        if len(outputs) == 0:
            output_functional = None
        elif len(outputs) == 1:
            output_functional = outputs[0]
        else:
            from pymor.operators.block import BlockColumnOperator
            output_functional = BlockColumnOperator(outputs)

        # visualizer
        if d == 1:
            visualizer = DuneGDT1dMatplotlibVisualizer(space) # only for stationary problems!
        else:
            visualizer = DuneGDTK3dVisualizer(space) if is_jupyter() else DuneGDTParaviewVisualizer(space)

        m  = StationaryModel(L, F, output_functional=output_functional, products=products, visualizer=visualizer,
                             name=f'{p.name}_dunegdt_P{order}CG')

        # for convenience: an interpolation of data functions into the solution space
        space_interpolation_points = space.interpolation_points() # cache
        def interpolate(func):
            df = DiscreteFunction(space, la_backend)
            np_view = np.array(df.dofs.vector, copy=False)
            np_view[:] = func.evaluate(space_interpolation_points)[:].ravel()
            return m.solution_space.make_array([df.dofs.vector,])

        data = {'grid': grid,
                'boundary_info': boundary_info,
                'space': space,
                'interpolate': interpolate}

        if preassemble:
            data['unassembled_m'] = m
            m = preassemble_(m)

        return m, data


    def discretize_instationary_cg(analytical_problem, diameter=None, domain_discretizer=None, grid_type=None,
                                   grid=None, boundary_info=None, num_values=None, time_stepper=None, nt=None,
                                   order=1, data_approximation_order=2, la_backend=Istl(),
                                   advection_in_divergence_form=False, mu_energy_product=None,
                                   ensure_consistent_initial_values=1e-6):
        """Discretizes an |InstationaryProblem| with a |StationaryProblem| as stationary part
        with dune-gdt using finite elements.

        Parameters
        ----------
        analytical_problem
            The |InstationaryProblem| to discretize.
        diameter
            If not `None`, `diameter` is passed as an argument to the `domain_discretizer`.
        domain_discretizer
            Discretizer to be used for discretizing the analytical domain. This has
            to be a function `domain_discretizer(domain_description, diameter, ...)`.
            If `None`, :meth:`pymor.discretizers.dunegdt.domaindiscretizers.default.discretize_domain_default`
            is used.
        grid_type
            If not `None`, this parameter (has to be `simplex` or `cube`) is forwarded to
            `domain_discretizer` to specify the type of the generated |Grid|.
        grid
            Instead of using a domain discretizer, the |Grid| can also be passed directly using this
            parameter.
        boundary_info
            A |BoundaryInfo| specifying the boundary types of the grid boundary entities. Must be
            provided if `grid` is specified.
        num_values
            The number of returned vectors of the solution trajectory. If `None`, each
            intermediate vector that is calculated is returned.
        time_stepper
            The :class:`time-stepper <pymor.algorithms.timestepping.TimeStepper>`
            to be used by :class:`~pymor.models.basic.InstationaryModel.solve`.
        nt
            If `time_stepper` is not specified, the number of time steps for implicit
            Euler time stepping.
        order
            Order of the Finite Element space.
        data_approximation_order
            Polynomial order (on each grid element) for the interpolation of the data functions.
        la_backend
            Tag to determine which linear algebra backend from dune-xt is used.
        advection_in_divergence_form
            If true, treats linear advection as advertised in StationaryProblem (i.e.
            :math:`∇ ⋅ (v u)`), else as in :math:`v ⋅∇ u` (where :math:`v` denotes the vector
            field).
        mu_energy_product
            If specified, converted to a |Mu| and used to assemble an appropriate energy product.
        ensure_consistent_initial_values
            If provided (and if non-trivial Dirichlet data is present resulting in a shifted model),
            the interpolated initial values are ensured to belong to H^1_0.

        Returns
        -------
        m
            The |Model| that has been generated.
        data
            Dictionary with the following entries:

                :grid:                  The generated grid from dune.xt.grid.
                :boundary_info:         The generated boundary info from dune.xt.grid.
                :space:                 The generated approximation space from dune.gdt.
                :interpolate:           To interpolate data functions in the solution space.
                :dirichlet_shift:       A |VectorArray| respresenting the Dirichlet shift.
                :unshifted_visualizer:  A visualizer which does not add the dirichlet_shift.
        """

        assert isinstance(analytical_problem, InstationaryProblem)
        assert isinstance(analytical_problem.stationary_part, StationaryProblem)
        assert (time_stepper is None) != (nt is None)
        assert ensure_consistent_initial_values is None \
               or (isinstance(ensure_consistent_initial_values, Number) \
                   and not ensure_consistent_initial_values < 0)

        # convert problem: creates grid, boundary info and checks and converts all data functions
        p = InstationaryDuneProblem.from_pymor(
                analytical_problem,
                data_approximation_order=data_approximation_order,
                diameter=diameter, domain_discretizer=domain_discretizer,
                grid_type=grid_type, grid=grid, boundary_info=boundary_info)

        return _discretize_instationary_cg_dune(
                p, num_values=num_values, time_stepper=time_stepper, nt=nt, order=order, la_backend=la_backend,
                advection_in_divergence_form=advection_in_divergence_form, mu_energy_product=mu_energy_product,
                ensure_consistent_initial_values=ensure_consistent_initial_values)


    def _discretize_instationary_cg_dune(dune_problem, num_values=None, time_stepper=None, nt=None, order=1,
                                         la_backend=Istl(), advection_in_divergence_form=True, mu_energy_product=None,
                                         ensure_consistent_initial_values=1e-6):
        assert isinstance(dune_problem, InstationaryDuneProblem)
        p = dune_problem

        m, data = _discretize_stationary_cg_dune(p.stationary_part, order=order, la_backend=la_backend,
                advection_in_divergence_form=advection_in_divergence_form, mu_energy_product=mu_energy_product)

        assert not p.initial_data.parametric
        if p.initial_data is not None:
            assert len(p.initial_data.functions) == 1
            assert len(p.initial_data.coefficients) == 1
            assert p.initial_data.coefficients[0] == 1
            initial_data = DuneGridFunction(p.initial_data.functions[0])

        if time_stepper is None:
            if p.stationary_part.diffusion is None:
                time_stepper = ExplicitEulerTimeStepper(nt=nt)
            else:
                time_stepper = ImplicitEulerTimeStepper(nt=nt)

        mass = m.l2_0_product

        # treatment of initial values
        grid, space = data['grid'], data['space']
        interpolated_initial_data = default_interpolation(GF(grid, initial_data.impl), space)

        # visualizer
        if grid.dimension == 1:  # slow due to copy, but only working one with time-series in 1d
            m = m.with_(visualizer=DuneGDT1dAsNumpyVisualizer(space, grid))

        m = InstationaryModel(
                operator=m.operator, rhs=m.rhs, mass=mass,
                initial_data=interpolated_initial_data,
                T=p.T,
                products=m.products,
                output_functional=m.output_functional,
                time_stepper=time_stepper,
                visualizer=m.visualizer,
                num_values=num_values,
                name=f'{p.name}_dunegdt_P{order}CG')

        return m, data
