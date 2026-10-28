/* Fotohaus.exe and fotohaus-cli.exe: start the bundled Python and run Fotohaus.
 *
 * Compiled by packaging/build_windows.py with the Python headers from the standalone CPython:
 *   GUI (Fotohaus.exe, no console window):  cl /DFOTOHAUS_GUI launcher.c ... /SUBSYSTEM:WINDOWS
 *   CLI (fotohaus-cli.exe, console):         cl launcher.c ... /SUBSYSTEM:CONSOLE
 *
 * Python runs isolated (PYTHON* environment variables and the user's site-packages are ignored)
 * from the folder this executable is in: Lib\, DLLs\ and python312.dll sit next to it.
 * Copyright (C) 2026 Abdullah Yusuf. GPL-3.0-or-later.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <windows.h>
#include <stdlib.h>

#ifdef FOTOHAUS_GUI
/* The app writes no console output; a crash is logged to %LOCALAPPDATA%\Fotohaus\Fotohaus.log. */
static const wchar_t *BOOTSTRAP =
    L"import os, runpy, sys\n"
    L"try:\n"
    L"    runpy.run_module('fotohaus', run_name='__main__', alter_sys=True)\n"
    L"except SystemExit:\n"
    L"    raise\n"
    L"except BaseException:\n"
    L"    import traceback\n"
    L"    d = os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'Fotohaus')\n"
    L"    os.makedirs(d, exist_ok=True)\n"
    L"    with open(os.path.join(d, 'Fotohaus.log'), 'a', encoding='utf-8') as f:\n"
    L"        traceback.print_exc(file=f)\n"
    L"    raise\n";
#endif

static void fail(PyConfig *cfg, PyStatus st)
{
    PyConfig_Clear(cfg);
#ifdef FOTOHAUS_GUI
    wchar_t msg[512];
    _snwprintf_s(msg, 512, _TRUNCATE, L"Fotohaus couldn't start Python:\n%hs\n\nPlease reinstall Fotohaus.",
                 st.err_msg ? st.err_msg : "unknown error");
    MessageBoxW(NULL, msg, L"Fotohaus", MB_ICONERROR | MB_OK);
#endif
    Py_ExitStatusException(st);
}

static int run(int argc, wchar_t **argv)
{
    wchar_t home[MAX_PATH];
    DWORD n = GetModuleFileNameW(NULL, home, MAX_PATH);
    if (n == 0 || n >= MAX_PATH)
        return 1;
    wchar_t *slash = wcsrchr(home, L'\\');
    if (slash)
        *slash = L'\0';

    PyConfig cfg;
    PyConfig_InitIsolatedConfig(&cfg);
    cfg.write_bytecode = 0;            /* the install folder may be read-only; .pyc are precompiled */
    cfg.parse_argv = 0;                /* every argument belongs to Fotohaus, not to Python */
    PyStatus st = PyConfig_SetString(&cfg, &cfg.home, home);
    if (PyStatus_Exception(st))
        fail(&cfg, st);
    st = PyConfig_SetArgv(&cfg, argc, argv);
    if (PyStatus_Exception(st))
        fail(&cfg, st);
#ifdef FOTOHAUS_GUI
    st = PyConfig_SetString(&cfg, &cfg.run_command, BOOTSTRAP);
#else
    st = PyConfig_SetString(&cfg, &cfg.run_module, L"fotohaus.cli");
#endif
    if (PyStatus_Exception(st))
        fail(&cfg, st);
    st = Py_InitializeFromConfig(&cfg);
    if (PyStatus_Exception(st))
        fail(&cfg, st);
    PyConfig_Clear(&cfg);
    return Py_RunMain();
}

#ifdef FOTOHAUS_GUI
int WINAPI wWinMain(HINSTANCE inst, HINSTANCE prev, PWSTR cmdline, int show)
{
    (void)inst; (void)prev; (void)cmdline; (void)show;
    return run(__argc, __wargv);
}
#else
int wmain(int argc, wchar_t **argv)
{
    return run(argc, argv);
}
#endif
