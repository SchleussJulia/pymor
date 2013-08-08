# This file is part of the pyMor project (http://www.pymor.org).
# Copyright Holders: Felix Albrecht, Rene Milk, Stephan Rave
# License: BSD 2-Clause License (http://opensource.org/licenses/BSD-2-Clause)

from __future__ import absolute_import, division, print_function

from pymor.core.interfaces import BasicInterface
from pymortests.base import (TestInterface, _load_all)

import pytest
import itertools

def implementors(interface_type):
    try:
        _load_all()
    except ImportError:
        pass
    return [T for T in interface_type.implementors(True) if not (T.has_interface_name() or 
                                                                 issubclass(T, TestInterface))] 

def subclasses_of(interface_type,**kwargs):
    return pytest.fixture(params=implementors(interface_type), **kwargs)

def grid_instances(interface_type, **kwargs):
    return pytest.fixture(params=list(itertools.chain(*(i.test_instances() for i in implementors(interface_type)))),
                           **kwargs)

@subclasses_of(BasicInterface)
def basicinterface_subclasses(request):
    return request.param
    

if __name__ == '__main__':
    pass
