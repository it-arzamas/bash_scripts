#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Check given OpenAPI specification and it's implementation requests and responses"""
import argparse
import json
import os
import re
import sys
from urllib.parse import urlparse, parse_qsl
import yaml

import requests
from colors import color
from jsonschema.exceptions import RefResolutionError
from werkzeug.datastructures import ImmutableMultiDict

from openapi_spec_validator import openapi_v3_spec_validator
from openapi_spec_validator.handlers import UrlHandler

from openapi_core import create_spec
from openapi_core.shortcuts import RequestValidator, ResponseValidator
from openapi_core.wrappers.base import BaseOpenAPIRequest, BaseOpenAPIResponse


class RequestsOpenAPIRequest(BaseOpenAPIRequest):
    def __init__(self, request, path_pattern=None, path_params=None):
        self.request = request
        self.url = urlparse(request.url)
        self._path_pattern = path_pattern
        self._path_params = {} if path_params is None else path_params

    @property
    def host_url(self):
        return self.url.scheme + '://' + self.url.netloc

    @property
    def path(self):
        return self.url.path

    @property
    def method(self):
        return self.request.method.lower()

    @property
    def path_pattern(self):
        if self._path_pattern is None:
            return self.url.path

        return self._path_pattern

    @property
    def parameters(self):
        return {
            'path': self._path_params,
            'query': ImmutableMultiDict(parse_qsl(self.url.query)),
            'headers': self.request.headers,
            'cookies': self.request.cookies,
        }

    @property
    def body(self):
        return self.request.data

    @property
    def mimetype(self):
        return self.request.headers.get('content-type')


class RequestsOpenAPIResponse(BaseOpenAPIResponse):
    def __init__(self, response):
        self.response = response

    @property
    def data(self):
        return self.response.text

    @property
    def status_code(self):
        return self.response.status_code

    @property
    def mimetype(self):
        return self.response.headers.get('content-type')


def load_spec(url):
    """Return OpenAPI specification dictionary for given URL"""
    handler = UrlHandler('http', 'https', 'file')
    if not urlparse(url).scheme:
        url = 'file://' + os.path.join(os.getcwd(), url)
    return handler(url), url


def load_parameters(filepath):
    """Return dictionary with parameters values for given filepath to substitute when validate dynamic API requests"""
    with open(filepath, 'r') as f:
        return yaml.safe_load(f)


def validate_specification(spec, spec_url):
    """This function validates specification file (usually openapi.yaml) or url"""
    counter = 0
    try:
        for i in openapi_v3_spec_validator.iter_errors(spec, spec_url=spec_url):
            counter += 1
            print_error(counter, ':'.join(i.absolute_path), i.message, i.instance)

    except RefResolutionError as e:
        counter += 1
        print_error(counter, '', 'Unable to resolve {} in {}'.format(e.__context__.args[0], e.args[0]), '')

    except BaseException:
        counter += 1
        print_error(counter, '', sys.exc_info()[0], '')

    finally:
        if counter > 0:
            print()
            print(color(' [FAIL] {:d} errors found '.format(counter), fg='white', bg='red', style='bold'))
            return 1
        else:
            print(color(' [PASS] No errors found ', fg='white', bg='green', style='bold'))
            return 0


def path_parameter_substitute(path, parameters):
    """This generator return path with substituted path parameters and continue to return such pathes for given path
    until parameters for substitution run out"""
    match = re.search('{(.+?)}', path)
    if match:
        if path in parameters['paths'] and 'path_parameters' in parameters['paths'][path]:
            # Path has path parameters (templates), calculate them first or skip path from testing
            for params_json_string in parameters['paths'][path]['path_parameters']:
                params = json.loads(params_json_string)
                for parameter in match.groups():
                    missing_parameters = []
                    real_path = path
                    if parameter in params:
                        real_path = real_path.replace('{' + parameter + '}', str(params[parameter]))
                    else:
                        missing_parameters.append(parameter)
                        print('Path parameter {} does not exists in given parameters to substitute!'.format(parameter))
                if missing_parameters:
                    print('Skipping, this path has {} unspecified path parameters (templates) in URL'
                          .format(len(missing_parameters)))
                    print()
                    continue
                yield real_path, path, {parameter: params[parameter]}
    else:
        yield path, path, {}


def validate_request(req, path_pattern, path_params, spec, url):
    print('Validating URL: {}'.format(url))
    openapi_request = RequestsOpenAPIRequest(req, path_pattern, path_params)
    validator = RequestValidator(spec)
    result = validator.validate(openapi_request)
    request_errors = result.errors

    r = req.prepare()
    s = requests.Session()
    res = s.send(r)

    openapi_response = RequestsOpenAPIResponse(res)
    validator = ResponseValidator(spec)
    result = validator.validate(openapi_request, openapi_response)
    response_errors = result.errors

    print('Request errors: {} Response errors: {}'.format(request_errors, response_errors))
    if request_errors or response_errors:
        errors_count = len(request_errors) + len(response_errors)
        print(color(' [FAIL] {:d} errors found '.format(errors_count), fg='white', bg='red', style='bold'))
        print("Response body: {}".format(res.text))
    else:
        errors_count = 0
        print(color(' [PASS] No errors found ', fg='white', bg='green', style='bold'))
    print()
    return errors_count


def validate_requests_and_responses(spec_dict, api_url, parameters=None):
    parameters = parameters if parameters else {'paths': {}}
    spec = create_spec(spec_dict)
    total_errors_count = 0

    for path, path_object in spec.paths.items():
        for method, operation in path_object.operations.items():
            print('{} {}'.format(method.upper(), path))
            for real_path, path_pattern, path_params in path_parameter_substitute(path, parameters):
                if method == 'get':
                    if path in parameters['paths'] and 'get' in parameters['paths'][path]:
                        for params_json_string in parameters['paths'][path]['get']:
                            url = api_url + real_path
                            req = requests.Request('GET', url, params=json.loads(params_json_string))
                            total_errors_count += validate_request(req, path_pattern, path_params, spec, url)
                    else:
                        url = api_url + real_path
                        req = requests.Request('GET', url)
                        total_errors_count += validate_request(req, path_pattern, path_params, spec, url)
                elif method == 'post':
                    if path in parameters['paths'] and 'post' in parameters['paths'][path]:
                        for params_json_string in parameters['paths'][path]['post']:
                            url = api_url + real_path
                            req = requests.Request('POST', url, data=params_json_string.strip(),
                                                   headers={'content-type': 'application/json'})
                            total_errors_count += validate_request(req, path_pattern, path_params, spec, url)
                    else:
                        print('Skipping, POST method has no example payload to test')
                        print()
                        continue
                else:
                    print('Skipping, no GET or POST methods defined for this path')
                    print()
                    continue

    if total_errors_count:
        print()
        print(color(' [FAIL] Total {:d} errors found '.format(total_errors_count), fg='white', bg='red',
                    style='bold'))
        return 1
    else:
        return 0


def print_error(count, path, message, instance):
    print()
    print(color('Error #{:d} in [{}]:'.format(count, path or 'unknown'), style='bold'))
    print("    {}".format(message))
    print("    {}".format(instance))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Check OpenAPI specification and check API requests and responses are '
                                                 'fits with it')

    parser.add_argument('openapi_specification_url', metavar='SPEC_FILE_OR_URL', type=str, default='',
                        help='OpenAPI specification filename or URL, for example, openapi.yaml or '
                             'https://raw.githubusercontent.com/OAI/OpenAPI-Specification/'
                             'master/examples/v3.0/petstore.yaml')
    parser.add_argument('--api', metavar='http://127.0.0.1:8080', help='Implemented API URL')
    parser.add_argument('--parameters', metavar='parameters.yml', help='Parameters values for substitution')
    args = parser.parse_args()

    spec_errors_count = rr_errors_count = 0

    if args.openapi_specification_url:
        print(color('Validating specification file...', style='bold', bg='cyan', fg='white'))
        spec, url = load_spec(args.openapi_specification_url)
        spec_errors_count = validate_specification(spec, url)

        if args.api:
            print()
            print()
            print(color('Validating requests and responses...', style='bold', bg='cyan', fg='white'))
            params = load_parameters(args.parameters) if args.parameters else None
            rr_errors_count = validate_requests_and_responses(spec, args.api, params)

    exit(spec_errors_count + rr_errors_count)
