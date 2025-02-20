# This file is part of the pyMOR project (https://www.pymor.org).
# Copyright pyMOR developers and contributors. All rights reserved.
# License: BSD 2-Clause License (https://opensource.org/licenses/BSD-2-Clause)

import numpy as np
import pytest
from hypothesis import given

import pymortests.strategies as pyst
from pymor.analyticalproblems.functions import ConstantFunction
from pymor.parameters.base import Mu, Parameters
from pymortests.base import runmodule

pytestmark = pytest.mark.builtin


num_samples = 100


@pytest.fixture(scope='module', params=([1], [2], [1, 1]))
def space(request):
    parameter_sizes = request.param
    param_dict = {'diffusion_' + str(ind): size for ind, size in enumerate(parameter_sizes)}
    return Parameters(param_dict).space(0.1, 1)


def test_uniform(space):
    values = space.sample_uniformly(num_samples)
    total_num_parameters = sum([space.parameters[k] for k in space.parameters])
    assert len(values) == num_samples**total_num_parameters
    for value in values:
        assert space.contains(value)


def test_randomly(space):
    values = space.sample_randomly(num_samples)
    assert len(values) == num_samples
    for value in values:
        assert space.contains(value)


def test_logarithmic_uniformly(space):
    values = space.sample_logarithmic_uniformly(num_samples)
    total_num_parameters = sum([space.parameters[k] for k in space.parameters])
    assert len(values) == num_samples**total_num_parameters
    for value in values:
        assert space.contains(value)


def test_logarithmic_randomly(space):
    values = space.sample_logarithmic_randomly(num_samples)
    assert len(values) == num_samples
    for value in values:
        assert space.contains(value)


def test_randomly_without_count(space):
    mu = space.sample_randomly()
    assert isinstance(mu, Mu)


def test_clip(space):
    from copy import deepcopy
    params = space.parameters
    upper_mu = {k: [space.ranges[k][1]] * params[k] for k in params}
    lower_mu = {k: [space.ranges[k][0]] * params[k] for k in params}
    large_mu = deepcopy(upper_mu)
    large_mu[next(iter(large_mu))][0] += 1.
    small_mu = deepcopy(lower_mu)
    small_mu[next(iter(small_mu))][0] -= 1.
    clipped_large_mu = space.clip(Mu(large_mu))
    clipped_small_mu = space.clip(Mu(small_mu))
    assert clipped_large_mu == Mu(upper_mu)
    assert clipped_small_mu == Mu(lower_mu)

    additional_upper_mu = upper_mu.copy()
    additional_upper_mu[next(iter(upper_mu)) + '_test'] = 2
    additional_large_mu = large_mu.copy()
    additional_large_mu[next(iter(large_mu)) + '_test'] = 2
    additional_mu = space.clip(Mu(additional_large_mu), keep_additional=True)
    no_additional_mu = space.clip(Mu(additional_large_mu), keep_additional=False)
    assert additional_mu == Mu(additional_upper_mu)
    assert no_additional_mu == Mu(upper_mu)


def test_parse_parameter():
    parameters = Parameters(b=2, a=1)
    mu_as_list = [1, 2, 3]
    mu_as_parameter_and_back = list(parameters.parse(mu_as_list).to_numpy())
    assert mu_as_list == mu_as_parameter_and_back


def test_parse_parameter_time_dep():
    parameters = Parameters(b=2, a=1)
    mu = parameters.parse([7, 't**2', 't[0]'])
    assert list(mu.with_(t=3)['a']) == [7]
    assert list(mu.with_(t=3)['b']) == [9, 3]


@given(pyst.mus)
def test_parse_mu(mu):
    parameters = mu.parameters
    assert parameters.parse(mu) == mu


@given(pyst.mus)
def test_mu_parameters(mu):
    params = mu.parameters
    assert isinstance(params, Parameters)
    assert mu.keys() == params.keys()
    assert params.is_compatible(mu)


@given(pyst.mus)
def test_mu_values(mu):
    assert all(isinstance(v, np.ndarray) for v in mu.values())
    assert all(v.ndim == 1 for v in mu.values())
    assert all(len(v) > 0 for v in mu.values())


@given(pyst.mus)
def test_mu_time_dependent(mu):
    for param in mu:
        func = mu.get_time_dependent_value(param)
        if mu.is_time_dependent(param):
            assert np.all(mu[param] == func(mu.get('t', 0)))
        else:
            assert isinstance(func, ConstantFunction)
            assert np.all(mu[param] == func.value)


@given(pyst.mus)
def test_mu_with_changed_time(mu):
    mu2 = mu.with_(t=42)
    for param in mu:
        if param == 't':
            assert mu2['t'].item() == 42
            continue
        func = mu.get_time_dependent_value(param)
        if mu.is_time_dependent(param):
            assert np.all(mu2[param] == func(42))
        else:
            assert np.all(mu[param] == mu2[param])


@given(pyst.mus)
def test_mu_to_numpy(mu):
    mu_array = mu.to_numpy()
    mu2 = mu.parameters.parse(mu_array)
    assert mu == mu2

@given(pyst.mus)
def test_mu_algebra(mu):
    mu_np = mu.to_numpy()
    other_np = np.ones(len(mu_np))
    other = mu.parameters.parse(other_np)

    assert mu + other == other + mu
    assert other_np + mu == other + mu_np
    assert all((mu + other).to_numpy() == (mu_np + other_np))
    assert all((mu - other).to_numpy() == (mu_np - other_np))
    assert all((other + mu).to_numpy() == (other_np + mu_np))
    assert all((other - mu).to_numpy() == (other_np - mu_np))
    assert all((-mu).to_numpy() == -mu_np)
    assert all((2. * mu).to_numpy() == 2. * mu_np)
    assert 2. * mu == mu * 2.


def test_mu_t_wrong_value():
    with pytest.raises(Exception):
        Mu(t=ConstantFunction(np.array([3])))
    with pytest.raises(Exception):
        Mu(t=np.array([1, 2]))


if __name__ == '__main__':
    runmodule(filename=__file__)
