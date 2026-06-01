import os
import site

block_cipher = None

site_packages = site.getsitepackages() + [site.getusersitepackages()]

a = Analysis(
    ['meetingscribe/app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('whisper_model', 'whisper_model'),
        ('/opt/homebrew/lib/python3.14/site-packages/faster_whisper/assets', 'faster_whisper/assets'),
        ('.env', '.'),
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
    icon=None,
    bundle_identifier='com.meetingscribe.app',
    info_plist={
        'LSUIElement': True,
        'CFBundleName': 'MeetingScribe',
        'CFBundleDisplayName': 'MeetingScribe',
        'CFBundleVersion': '0.1.0',
        'CFBundleShortVersionString': '0.1.0',
        'NSMicrophoneUsageDescription': 'MeetingScribe needs microphone access to record meetings.',
    },
)
