import os
import site

_version = os.environ.get('MS_VERSION', '0.1.0')
_profile = os.environ.get('MS_PROFILE', 'laurelle')
_bundle_id = os.environ.get('MS_BUNDLE_ID', 'com.meetingscribe.app')
_feed_url = os.environ.get('MS_FEED_URL', 'https://github.com/jacobWeeces/meetingscribe/releases/latest/download/appcast.xml')

block_cipher = None

site_packages = site.getsitepackages() + [site.getusersitepackages()]

a = Analysis(
    ['meetingscribe/app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('whisper_model', 'whisper_model'),
        ('/opt/homebrew/lib/python3.14/site-packages/faster_whisper/assets', 'faster_whisper/assets'),
    ],
    hiddenimports=[
        'rumps',
        'sounddevice',
        'numpy',
        'scipy',
        'scipy.io',
        'scipy.io.wavfile',
        'faster_whisper',
        'ctranslate2',
        'anthropic',
        'tokenizers',
        'onnxruntime',
        'huggingface_hub',
        '_sounddevice_data',
        'av',
        'AppKit',
        'objc',
        'Foundation',
        'PyObjCTools',
        'Security',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'torch', 'torchvision', 'torchaudio',
        'tensorflow', 'tensorboard', 'keras',
        'pandas', 'pyarrow',
        'cv2', 'opencv',
        'matplotlib', 'plotly',
        'PIL', 'Pillow',
        'sqlalchemy',
        'pytest', 'py',
        'IPython', 'ipykernel', 'jupyter',
        'transformers',
        'datasets',
        'numba', 'llvmlite',
        'openpyxl',
        'lxml',
        'pygments',
        'pdfminer', 'pypdfium2',
        'uvicorn', 'uvloop', 'websockets',
        'tkinter',
        'multiprocess',
        'rich',
        'jsonschema',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MeetingScribe',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    target_arch='arm64',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='MeetingScribe',
)

app = BUNDLE(
    coll,
    name='MeetingScribe.app',
    icon='assets/MeetingScribe.icns',
    bundle_identifier=_bundle_id,
    info_plist={
        'LSUIElement': True,
        'CFBundleName': 'MeetingScribe',
        'CFBundleDisplayName': 'MeetingScribe',
        'CFBundleVersion': _version,
        'CFBundleShortVersionString': _version,
        'NSMicrophoneUsageDescription': 'MeetingScribe needs microphone access to record meetings.',
        'SUFeedURL': _feed_url,
        'MSUserProfile': _profile,
        'SUPublicEDKey': 'SP/964iRTWkRNR91DJNIpPBGrxIu1/IHSr+JnE6GkvA=',
        'SUEnableAutomaticChecks': True,
        'SUScheduledCheckInterval': 86400,
        'SUEnableInstallerLauncherService': True,
    },
)
