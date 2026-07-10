from setuptools import setup, find_packages

setup(
    name="meaxure-converter",
    version="1.2.0",
    description="Convert Sketch MeaXure HTML exports to native HTML / WeChat Mini Program",
    packages=find_packages(),
    python_requires=">=3.9",
    install_requires=["Pillow>=10.0.0"],
    entry_points={
        "console_scripts": [
            "meaxure-convert=meaxure_converter.cli:main",
        ],
    },
)
