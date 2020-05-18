# -*- coding: UTF-8 -*-

from setuptools import setup, find_packages

setup(name="tact",
      version="0.1pre",
      description="Tool for performing multivariate classificaton on top"
      "analyses",
      url="https://github.com/brunel-physics/tact",
      author="Corin Hoad",
      author_email="c.h@cern.ch",
      license="BSD",
      packages=find_packages(),
      entry_points={
          "console_scripts": ["tact=tact.tact:main"],
      },
      install_requires=[
          "dill==0.2.8.2",
          "matplotlib==2.2.2",
          "numpy==1.14.2",
          "pandas==0.23.3",
          "PyYAML==5.1",
          "root_numpy==4.7.3",
          "root_pandas==0.6.1",
          "scikit_learn==0.19.2",
          "scipy==1.1.0",
      ],
      extras_require={
          'MLP':  ["Keras==2.2.0"],
          'xgboost': ["xgboost==0.71"],
          'lightgbm': ["lightgbm"]
      },
      zip_safe=False)
