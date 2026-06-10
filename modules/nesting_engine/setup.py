from setuptools import setup, Extension
from Cython.Build import cythonize

# Aquí forzamos a que el módulo se llame simplemente "algorithm"
extensiones = [
    Extension("algorithm", ["algorithm.pyx"])
]

setup(
    name='Motor Nesting C++',
    ext_modules=cythonize(extensiones, compiler_directives={'language_level': "3"})
)