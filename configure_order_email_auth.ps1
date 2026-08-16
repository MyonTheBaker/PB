$ErrorActionPreference = 'Stop'
Write-Host 'Park Bäckerei Order Control Tower - Gmail authentication' -ForegroundColor Cyan
Write-Host 'Enter the 16-character Google App Password for system@parkbaeckerei.com.'
Write-Host 'The value will not be displayed.'
$secure = Read-Host 'App password' -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    if ([string]::IsNullOrWhiteSpace($plain)) {
        throw 'No credential was entered.'
    }
    $plain = $plain.Replace(' ', '')
    [Environment]::SetEnvironmentVariable('ORDER_CONTROL_EMAIL_PASSWORD', $plain, 'User')
    Write-Host 'Credential saved to the Windows user environment.' -ForegroundColor Green
} finally {
    if ($pointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
    $plain = $null
    $secure = $null
}
Write-Host 'You may close this window and reply: saved'
Read-Host 'Press Enter to close'
