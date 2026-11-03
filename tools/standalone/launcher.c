/*
 * Heaven3D launcher for Windows: a tiny .exe (with the Heaven3D logo as its
 * icon) that starts the bundled Python next to it.  No console window.
 *
 *   HUB    : runtime\python\pythonw.exe -E -s Heaven3D_Hub.py
 *   EDITOR : runtime\python\pythonw.exe -E -s Heaven3D.py
 *   GAME   : engine\python\pythonw.exe  -E -s engine\Heaven3D.py --game "<folder>"
 *
 * Build (see build_standalone.py):
 *   x86_64-w64-mingw32-windres launcher.rc -O coff -o icon.o
 *   x86_64-w64-mingw32-gcc -DH3D_GAME -municode -mwindows -Os -s launcher.c icon.o -o game.exe
 */
#include <windows.h>
#include <wchar.h>

#if defined(H3D_GAME)
#  define PYREL L"engine\\python\\pythonw.exe"
#  define SCRIPT L"engine\\Heaven3D.py"
#  define WHAT L"game"
#elif defined(H3D_EDITOR)
#  define PYREL L"runtime\\python\\pythonw.exe"
#  define SCRIPT L"Heaven3D.py"
#  define WHAT L"Heaven3D editor"
#else
#  define PYREL L"runtime\\python\\pythonw.exe"
#  define SCRIPT L"Heaven3D_Hub.py"
#  define WHAT L"Heaven3D Hub"
#endif

static wchar_t dir[32768], py[32768], script[32768], cmd[65536];

static void fail(const wchar_t *msg, const wchar_t *path) {
    wchar_t buf[40000];
    swprintf(buf, 40000, L"%ls\n\n%ls\n\nPlease extract the whole folder (all files) and run it again.", msg, path);
    MessageBoxW(NULL, buf, L"Heaven3D", MB_ICONERROR | MB_OK);
}

int WINAPI wWinMain(HINSTANCE hi, HINSTANCE hp, PWSTR args, int show) {
    (void)hi; (void)hp; (void)show;
    DWORD n = GetModuleFileNameW(NULL, dir, 32767);
    if (n == 0 || n >= 32767) return 1;
    wchar_t *slash = wcsrchr(dir, L'\\');
    if (slash) *slash = 0;
    swprintf(py, 32768, L"%ls\\%ls", dir, PYREL);
    swprintf(script, 32768, L"%ls\\%ls", dir, SCRIPT);
    if (GetFileAttributesW(py) == INVALID_FILE_ATTRIBUTES) { fail(L"The bundled Python runtime is missing:", py); return 2; }
    if (GetFileAttributesW(script) == INVALID_FILE_ATTRIBUTES) { fail(L"The " WHAT L" files are missing:", script); return 3; }
#if defined(H3D_GAME)
    swprintf(cmd, 65536, L"\"%ls\" -E -s \"%ls\" --game \"%ls\" %ls", py, script, dir, args ? args : L"");
#else
    swprintf(cmd, 65536, L"\"%ls\" -E -s \"%ls\" %ls", py, script, args ? args : L"");
#endif
    STARTUPINFOW si; PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof si); si.cb = sizeof si;
    ZeroMemory(&pi, sizeof pi);
    if (!CreateProcessW(py, cmd, NULL, NULL, FALSE, 0, NULL, dir, &si, &pi)) {
        fail(L"Could not start:", py);
        return 4;
    }
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 0;
}
