# Store a FRED key locally without echoing it or putting it in command history.
# Run this yourself after requesting the key in your authenticated FRED account.
# This changes only the current user's environment and the current PowerShell session.
$ErrorActionPreference = 'Stop'
$fredSecureInput = Read-Host 'Paste your FRED API key (input is hidden)' -AsSecureString
$fredBuffer = [IntPtr]::Zero
try {
    $fredBuffer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($fredSecureInput)
    $fredPlainInput = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($fredBuffer)
    if ($fredPlainInput -cnotmatch '^[a-z0-9]{32}$') {
        throw 'The FRED key must contain exactly 32 lowercase letters or digits. No environment setting was changed.'
    }
    if ($fredPlainInput -ceq 'abcdefghijklmnopqrstuvwxyz123456') {
        throw 'The documentation example is not a registered API key. No environment setting was changed.'
    }
    [Environment]::SetEnvironmentVariable('FRED_API_KEY', $fredPlainInput, 'User')
    [Environment]::SetEnvironmentVariable('FRED_API_KEY', $fredPlainInput, 'Process')
    Write-Host 'FRED_API_KEY is saved for your Windows user and this PowerShell session. Its value has not been printed.'
    Write-Host 'Restart Codex before using the key there, so it inherits the new environment setting.'
} finally {
    if ($fredBuffer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($fredBuffer)
    }
    $fredPlainInput = $null
    $fredSecureInput.Dispose()
}
