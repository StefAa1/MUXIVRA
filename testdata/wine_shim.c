/* Wine-only shim: Windows 10+ provides bcryptprimitives.dll natively; Wine 8 does not.
   Used ONLY to verify the portable bundle under Wine, never shipped. */
#include <windows.h>
#include <bcrypt.h>

__declspec(dllexport) BOOLEAN ProcessPrng(PUCHAR pbData, size_t cbData)
{
    NTSTATUS status = BCryptGenRandom(NULL, pbData, (ULONG)cbData,
                                      BCRYPT_USE_SYSTEM_PREFERRED_RNG);
    return status == 0 ? TRUE : FALSE;
}
