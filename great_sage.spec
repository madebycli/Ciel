# PyInstaller build spec for Great Sage.
#
#   py -m PyInstaller great_sage.spec --noconfirm
#
# ONEDIR, not onefile, deliberately:
#   * onefile unpacks the whole bundle to a temp folder on every launch.
#     With torch in it that is several GB of copying before the window
#     appears, every single time.
#   * that temp folder is deleted on exit, so memory.txt, hud_settings.json
#     and the voice preferences would not survive a restart.
#
# What is NOT in here, because it cannot be:
#   * Ollama and its chat model - a separate service, installed system-wide.
#   * The F5/Whisper weights (~3.6GB) - fetched to the HuggingFace cache on
#     first use. Bundling them would roughly double the download for
#     something the app retrieves by itself.
# installer.py explains both to the user and installs what it can.

import os
from PyInstaller.utils.hooks import (collect_data_files, collect_dynamic_libs,
                                     collect_submodules)

block_cipher = None

# Assets the app reads at runtime. Kept at the top level of the bundle so
# the relative paths in config/settings.py resolve unchanged.
binaries = []
datas = [
    ('hud_prototype.html', '.'),
    ('installer.html', '.'),
    ('log_console.html', '.'),
    ('voice_samples', 'voice_samples'),
    ('voice_lines', 'voice_lines'),
    # Three.js, vendored rather than fetched from a CDN - without it
    # the packaged app renders nothing on a machine with no internet.
    ('vendor', 'vendor'),
]

# Fail the BUILD, loudly, if any voice asset the app actually references
# is missing or lives outside the folders bundled above.
#
# The Japanese set previously pointed at "../sounds/voice" - outside the
# project entirely - so it worked on the machine the clips were recorded
# on and silently fell through to synthesis everywhere else. A packaged
# build has no parent "sounds" folder. Nothing surfaced that: the app
# starts, speaks, and just never plays a pre-recorded line.
#
# Checked here rather than trusted, because the failure is invisible at
# runtime and only shows up as "the voice lines don't work on my machine".
def _verify_voice_assets():
    import sys as _sys
    _sys.path.insert(0, os.path.abspath('.'))
    from great_sage.config import settings as _s

    problems = []
    for _set_name, _lines in getattr(_s, 'VOICE_LINE_SETS', {}).items():
        for _pattern, _path in _lines:
            if not os.path.exists(_path):
                problems.append(f"{_set_name}: missing file {_path}")
            elif os.path.normpath(_path).startswith('..'):
                problems.append(
                    f"{_set_name}: {_path} is outside the project and will "
                    f"NOT be bundled")

    _cand = _s.VOICE_CANDIDATES_DIR
    if not os.path.isdir(_cand):
        problems.append(f"voice candidates folder missing: {_cand}")
    else:
        _clones = [f for f in os.listdir(_cand)
                   if f.endswith('.wav') and '_preview' not in f]
        if not _clones:
            problems.append(f"no voice clones found in {_cand}")
        # F5 transcribes a clip itself when its .txt sidecar is absent -
        # slower and less accurate, and easy to miss in a packaged build.
        for _c in _clones:
            if not os.path.exists(os.path.join(_cand, _c[:-4] + '.txt')):
                problems.append(f"clone {_c} has no transcript sidecar")
        print(f"[spec] {len(_clones)} voice clone(s) will be bundled")

    if not os.path.exists(_s.F5_REFERENCE_AUDIO_PATH):
        problems.append(
            f"active reference voice missing: {_s.F5_REFERENCE_AUDIO_PATH}")

    if problems:
        raise SystemExit("\n[spec] VOICE ASSET CHECK FAILED:\n  - "
                         + "\n  - ".join(problems))
    print("[spec] voice assets OK - all clips resolve inside the project")


_verify_voice_assets()

# f5_tts ships non-Python files it loads by path (vocab, model configs);
# without these the engine imports fine and then fails at first synthesis.
for pkg in ('f5_tts', 'vocos'):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass

# NATIVE LIBRARIES and non-Python DATA that the voice path loads by name.
#
# Both of these shipped broken in the first working build, and both were
# silent - the app started, answered, and simply did nothing audible.
#
# torchcodec: torchaudio.load() now routes through torchcodec, which
# dlopens libtorchcodec_core*.dll / _custom_ops*.dll / _image*.dll from
# its own package directory. None were bundled - only the .dist-info
# added above - so the DECODE step failed at first synthesis with
#   ImportError: No spec found for libtorchcodec_image
# and the reply was generated with no sound at all.
#
# faster_whisper: transcribe(vad_filter=True) needs
# assets/silero_vad_v6.onnx. Absent, nothing is detected as speech, so
# transcription returns "" and push-to-talk appears to work while Sage
# never hears anything. No error is raised anywhere.
for _pkg in ('torchaudio', 'torch'):
    try:
        binaries += collect_dynamic_libs(_pkg)
    except Exception:
        pass

# torchcodec is copied WHOLESALE, by hand, rather than via
# collect_dynamic_libs - which misses exactly the file that matters.
#
# torchcodec finds its libraries with
#   FileFinder(Path(__file__).parent).find_spec(lib_name)
# over EXTENSION_SUFFIXES + ['.dll', '.pyd'] - so every binary has to sit
# in the torchcodec package folder under its original name.
#
# collect_dynamic_libs globs .dll and got 14 of them, but skipped
# libtorchcodec_pybind_ops.PYD: PyInstaller treats .pyd as an importable
# extension module, and this one is never imported, only located by path.
# The result was a SECOND round of silent no-audio -
#   ImportError: No spec found for libtorchcodec_pybind_ops
# after the first fix had already moved the error from _image to here.
# The support codecs (jpeg8, libwebp*, zlib, libzstd ...) are pulled in
# for the same reason: they are loaded by name, not imported.
try:
    import glob as _glob
    import torchcodec as _tc
    _tc_dir = os.path.dirname(os.path.abspath(_tc.__file__))
    _tc_libs = (_glob.glob(os.path.join(_tc_dir, '*.dll'))
                + _glob.glob(os.path.join(_tc_dir, '*.pyd')))
    if not _tc_libs:
        raise SystemExit('torchcodec has no .dll/.pyd - refusing to build a '
                         'bundle whose replies would have no audio')
    binaries += [(_f, 'torchcodec') for _f in _tc_libs]
    print(f'[spec] torchcodec: {len(_tc_libs)} native libraries bundled')
except SystemExit:
    raise
except Exception as _exc:
    print(f'[spec] WARNING: torchcodec libraries not collected ({_exc}) - '
          f'the build will have NO VOICE')
for _pkg in ('faster_whisper', 'torchcodec'):
    try:
        datas += collect_data_files(_pkg)
    except Exception:
        pass

# PYTHON SOURCE for the packages that call torch.jit.script.
#
# torch.jit.script COMPILES a function by reading its source text back
# with inspect.getsourcelines(). A normal bundle carries only .pyc, so
# there is no source to read and the import dies with a bare
#   OSError: could not get source code
# from x_transformers/attend.py, where `@torch.jit.script def softclamp`
# runs at import time. That is not an ImportError, so the voice engine's
# import guard does not catch it either - the whole app failed to start,
# which is worse than the mute build it replaced.
#
# include_py_files=True ships the .py alongside, which is what
# inspect.getsourcelines needs. Only for packages that actually script
# something (verified by grepping each one), because it is pure size
# everywhere else.
for _pkg in ('x_transformers', 'einops'):
    try:
        datas += collect_data_files(_pkg, include_py_files=True)
    except Exception:
        pass

# Package METADATA (the .dist-info folders), which is not the same thing
# as bundling the module and is not implied by it.
#
# This is what made the first working build mute. torchcodec's module was
# in the bundle, so transformers' `find_spec("torchcodec")` said it was
# available - and then reading its version raised
#   PackageNotFoundError: No package metadata was found for torchcodec
# because dist-info had not been copied. transformers wraps that in
#   ModuleNotFoundError: Could not import module 'pipeline'
# f5_tts's `from transformers import pipeline` failed, the engine fell
# back to Pocket TTS, which is excluded below, and the app continued in
# text-only mode. Nothing on screen said why: it just never spoke.
#
# recursive=True so a dependency's metadata comes along too - the probe
# that fails is rarely the package you thought of.
try:
    from PyInstaller.utils.hooks import copy_metadata
    for _pkg in ('torch', 'torchcodec', 'torchaudio', 'torchvision',
                 'transformers', 'tokenizers', 'huggingface-hub',
                 'safetensors', 'accelerate', 'f5-tts', 'vocos',
                 'faster-whisper', 'x-transformers', 'ema-pytorch',
                 'torchdiffeq', 'cached-path', 'omegaconf', 'numpy',
                 'filelock', 'regex', 'tqdm', 'pyyaml', 'sentencepiece',
                 'soundfile', 'sounddevice', 'pedalboard', 'requests'):
        try:
            datas += copy_metadata(_pkg, recursive=True)
        except Exception:
            pass        # not installed, or no metadata - neither is fatal
except Exception:
    pass

hiddenimports = [
    # NOTE: the overlay host is deliberately NOT bundled here. It used to
    # be ('overlay_window' plus the PySide6 modules), and that was wrong.
    #
    # This bundle is built with Python 3.14, where the oldest installable
    # PySide6 is 6.10.1 - and 6.10 flickers. The overlay needs 6.4.3,
    # which does not support 3.14 at all. Bundling PySide6 from here
    # therefore could not produce a working overlay however carefully it
    # was configured: it would ship the flickering version, and the flaw
    # only becomes visible after watching the overlay for a while.
    #
    # The overlay is built separately by overlay.spec under the 3.11
    # interpreter and dropped into this bundle as overlay/ by build.py.
    # That also takes ~450MB of Qt out of this bundle, which the main
    # process never imports.
    # pywebview picks its GUI backend at runtime, so the import graph never
    # reaches it statically.
    'webview.platforms.winforms',
    'clr_loader',
    'pythonnet',
    # Likewise for the voice stack's plugin-style loading.
    'f5_tts.api',
    'f5_tts.infer.utils_infer',
    'vocos',
    'faster_whisper',
    'pedalboard',
    'soundfile',
    'sounddevice',
    # F5-TTS reaches these only at synthesis time - through hydra config
    # strings, or inside functions - so a static import scan never sees
    # them. The first build shipped f5_tts's .yaml configs with nothing
    # able to parse or execute them: the engine imported cleanly and then
    # raised at first use, which surfaced as the app simply having no
    # voice. Verified missing from that bundle, one by one.
    'cached_path',        # downloads the checkpoints
    'omegaconf',          # parses the hydra configs
    'x_transformers',     # the model architecture
    'ema_pytorch',
    'torchdiffeq',        # the flow-matching ODE solver
    'pypinyin',
    'unidecode',
]
# Packages that register subclasses dynamically, or that f5_tts pulls in
# by name. collect_submodules walks each one so nothing is missed the way
# the lazily-imported dependencies above were.
for pkg in ('torchaudio', 'transformers.models.whisper',
            'f5_tts', 'x_transformers', 'cached_path', 'omegaconf'):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass

# jieba and cached_path load dictionaries / config from their own package
# directories at runtime.
for pkg in ('cached_path', 'omegaconf'):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass

excludes = [
    # Superseded voice engines. Their modules stay in the tree behind the
    # VOICE_ENGINE switch and import lazily, so excluding the libraries
    # keeps them from dragging a gigabyte of transitive dependencies into
    # a build that never calls them.
    'TTS', 'coqui_tts', 'pocket_tts', 'pyttsx3', 'cutlet', 'fugashi',
    # Dev-only, never imported by the app.
    'PyInstaller', 'pytest', 'IPython', 'jupyter', 'notebook', 'tkinter',
    # PySide6, actively excluded rather than just left out. preflight.py
    # imports it to report whether overlay mode is available, and that
    # import alone is enough for PyInstaller to pull the whole of Qt in.
    # The overlay lives in its own bundle; see the note in hiddenimports.
    'PySide6', 'shiboken6', 'qtpy',
    # NOT matplotlib. It looks like an obvious cut for a windowed app that
    # draws nothing with it - but f5_tts imports it (verified: it is in the
    # set of modules `import f5_tts.api` pulls in). Excluding it made
    # `from f5_tts.api import F5TTS` raise ImportError inside the bundle,
    # which the engine reported as "install f5-tts" for a package that was
    # already present, and the app then fell back to a Pocket TTS also
    # excluded here - ending in silent text-only mode.
]

a = Analysis(
    ['app.py'],
    pathex=[os.path.abspath('.')],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='GreatSage',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX mangles some CUDA DLLs; the size win is not worth it
    console=False,      # windowed app - a console would flash on every launch
    disable_windowed_traceback=False,
    icon='icon.ico',    # 7 sizes, 16-256; see the note in the header
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, name='GreatSage',
)
