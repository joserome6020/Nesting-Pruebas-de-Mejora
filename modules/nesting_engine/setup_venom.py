from setuptools import setup, Extension
from setuptools.command.build_ext import build_ext
import sys
import setuptools

class get_pybind_include(object):
    def __str__(self):
        import pybind11
        return pybind11.get_include()

ext_modules = [
    Extension(
        'venom_core',
        ['venom_core.cpp'],
        include_dirs=[
            get_pybind_include(),
        ],
        language='c++'
    ),
]

setup(
    name='venom_core',
    version='1.0',
    description='Venom Polisher C++ Core',
    ext_modules=ext_modules,
    setup_requires=['pybind11>=2.5.0'],
)
