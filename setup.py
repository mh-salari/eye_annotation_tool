from setuptools import find_packages, setup

setup(
    name="eye_annotation_tool",
    version="0.2.1",
    packages=find_packages(),
    include_package_data=True,
    package_data={
        "annotation_app": ["resources/*"],
    },
    install_requires=[
        "PyQt5",
        "numpy",
        "opencv-python",
        "scipy",
        # Add any other dependencies your project needs
    ],
    entry_points={
        "console_scripts": [
            "eye_annotation_tool=annotation_app.main:run_app",
        ],
    },
    author="Mohammadhossein Salari",
    author_email="mohammadhossein.salari@gmail.com",
    description="A tool for annotating pupil and iris in eye images",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/mh-salari/eye_annotation_tool",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.6",
)
