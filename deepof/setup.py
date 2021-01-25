import setuptools

with open("../README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="deepof",
    version="0.1.0",
    author="Lucas Miranda",
    author_email="lucas_miranda@psych.mpg.de",
    description="deepof (Deep Open Field): Open Field animal pose classification tool ",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://gitlab.mpcdf.mpg.de/lucasmir/deepof/",
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Machine Learning :: Unsupervised Deep Learning",
        "Google :: Tensorflow",
        "Neuroscience :: Animal behaviour",
    ],
    python_requires=">3.7",
    platform="Platform independent",
    License="MIT",
    include_package_data=True,
    install_requires=[
        "tensorflow",
        "numpy",
        "pandas",
        "joblib",
        "matplotlib",
        "networkx",
        "opencv-python",
        "regex",
        "scikit-learn",
        "scipy",
        "seaborn",
        "sklearn",
        "tables",
        "tensorflow-probability",
        "tqdm",
        "umap-learn",
    ],
)
