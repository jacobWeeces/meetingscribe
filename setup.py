from setuptools import setup, find_packages

setup(
    name="meetingscribe",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "rumps>=0.4.0",
        "sounddevice>=0.5.0",
        "numpy>=2.0.0",
        "scipy>=1.14.0",
        "faster-whisper>=1.0.0",
        "anthropic>=0.80.0",
        "pyobjc-framework-Security>=10.0",
        "pyobjc-framework-ScreenCaptureKit>=12.2",
    ],
    entry_points={
        "console_scripts": [
            "meetingscribe=meetingscribe.app:main",
        ],
    },
    python_requires=">=3.10",
)
