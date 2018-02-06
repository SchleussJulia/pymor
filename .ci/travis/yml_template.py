#!/usr/bin/env python3

tpl = '''# THIS FILE IS AUTOGENERATED -- DO NOT EDIT #
#   Edit and Re-run .travis.yml.py instead  #


sudo: false
dist: trusty
language: python
services: docker

python:
  - 3.6

# necessary for version.py generation to function properly
git:
    depth: 1000

notifications:
  email:
    on_success: change
    on_failure: change
    on_start: never

branches:
  except:
    - gh-pages

stages:
    - test
    - deploy

before_script:
    - export IMAGE="pymor/testing:${DOCKER_TAG}"
    - docker pull ${IMAGE}
    - export ENV_FILE=${HOME}/env
    - printenv | \grep TRAVIS > ${ENV_FILE}
    - printenv | \grep PYTEST_MARKER >> ${ENV_FILE}
    - printenv | \grep encrypted >> ${ENV_FILE}
    - export DOCKER_RUN="docker run --privileged -e LOCAL_USER_ID=$(id -u) --env-file ${ENV_FILE} -v ${TRAVIS_BUILD_DIR}:/src ${IMAGE}"

script:
        - ${DOCKER_RUN} /src/.ci/travis/script.bash

# runs independent of 'script' failure/success
after_script:
        - ${DOCKER_RUN} /src/.ci/travis/after_script.bash

jobs:
  include:
  - stage: test
    env: PYTEST_MARKER="NUMPY" DOCKER_TAG="3.6"
{%- for py, m in matrix %}
  - stage: test
    env: PYTEST_MARKER="{{m}}" DOCKER_TAG="{{py}}"
{%- endfor %}

  - stage: deploy
    if: type IS push
    script: ./.ci/travis/deploy.bash
    # overwrite other global/matrix settings
    before_script: true
    after_script: true
    env: NOTHING=NONE
# THIS FILE IS AUTOGENERATED -- DO NOT EDIT #
#       Re-run .travis.yml.py instead       #
'''


import os
import jinja2
import sys
from itertools import product
tpl = jinja2.Template(tpl)
pythons = ['3.5', '3.6', '3.7-rc']
marker = [None, "PIP_ONLY", "MPI"]
with open(os.path.join(os.path.dirname(__file__), 'travis.yml'), 'wt') as yml:
    matrix = product(pythons, marker)
    yml.write(tpl.render(matrix=matrix))
