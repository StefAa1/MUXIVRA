/* MUXIVRA.exe — native launcher for the portable build.
   Starts the bundled pythonw.exe in desktop mode, or explains how to extract. */
#include <windows.h>
#include <string.h>

int WINAPI WinMain(HINSTANCE instance, HINSTANCE previous, LPSTR command, int show)
{
    char base[MAX_PATH];
    char pythonw[MAX_PATH];
    char params[4096];
    char message[MAX_PATH + 256];

    (void)instance;
    (void)previous;
    (void)show;

    if (!GetModuleFileNameA(NULL, base, MAX_PATH)) {
        MessageBoxA(NULL, "MUXIVRA: cannot determine its own folder.", "MUXIVRA", MB_ICONERROR);
        return 1;
    }
    char *slash = strrchr(base, '\\');
    if (slash) {
        *slash = '\0';
    }

    wsprintfA(pythonw, "%s\\runtime\\python\\pythonw.exe", base);
    if (GetFileAttributesA(pythonw) == INVALID_FILE_ATTRIBUTES) {
        wsprintfA(
            message,
            "MUXIVRA cannot start: the application files are not in this folder.\n\n"
            "1) Right-click the downloaded zip and choose Extract All\n"
            "2) Open the extracted MUXIVRA-portable-win64 folder\n"
            "3) Run MUXIVRA.exe from there\n\n"
            "Folder: %s",
            base);
        MessageBoxA(NULL, message, "MUXIVRA", MB_ICONERROR | MB_OK);
        return 1;
    }

    wsprintfA(params, "-m sshdeck desktop %s", command);
    HINSTANCE result = ShellExecuteA(NULL, "open", pythonw, params, base, SW_SHOWNORMAL);
    if ((INT_PTR)result <= 32) {
        wsprintfA(message, "MUXIVRA could not start the bundled Python (%s).\nError code %ld.", pythonw, (long)(INT_PTR)result);
        MessageBoxA(NULL, message, "MUXIVRA", MB_ICONERROR | MB_OK);
        return 1;
    }
    return 0;
}
