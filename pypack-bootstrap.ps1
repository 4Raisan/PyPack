# Used only by PyPack-Setup.bat when no supported Python is found.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$downloadDirectory = $null
try {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($winget) {
        & $winget.Source install --exact --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -eq 0) { exit 0 }
        Write-Host '[PyPack] WinGet failed; trying the official Python installer.'
    }
    $architecture = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    $suffix = switch ($architecture) {
        'AMD64' { 'amd64' }
        'ARM64' { 'arm64' }
        'x86' { '' }
        default { throw "Unsupported Windows architecture: $architecture" }
    }
    $listing = Invoke-WebRequest 'https://www.python.org/ftp/python/' -UseBasicParsing
    $versions = [regex]::Matches($listing.Content, 'href="(3\.[0-9]+\.[0-9]+)/"') |
        ForEach-Object { [version]$_.Groups[1].Value } |
        Where-Object { $_ -ge [version]'3.10.0' } |
        Sort-Object -Descending -Unique
    $installerUrl = $null
    foreach ($version in ($versions | Select-Object -First 20)) {
        $filename = if ($suffix) { "python-$version-$suffix.exe" } else { "python-$version.exe" }
        $candidateUrl = "https://www.python.org/ftp/python/$version/$filename"
        try {
            $null = Invoke-WebRequest $candidateUrl -Method Head -UseBasicParsing -TimeoutSec 15
            $installerUrl = $candidateUrl
            break
        } catch {
            # Security-only releases and unsupported architectures may have no installer.
            $response = $_.Exception.Response
            if (-not $response -or [int]$response.StatusCode -ne 404) { throw }
        }
    }
    if (-not $installerUrl) { throw 'No compatible stable Python installer found.' }
    $downloadDirectory = Join-Path ([IO.Path]::GetTempPath()) ('pypack-python-' + [guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $downloadDirectory
    $installerPath = Join-Path $downloadDirectory 'python-installer.exe'
    Write-Host "[PyPack] Downloading $installerUrl"
    Invoke-WebRequest $installerUrl -OutFile $installerPath -UseBasicParsing -TimeoutSec 300
    $signature = Get-AuthenticodeSignature -LiteralPath $installerPath
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
        throw 'Python installer signature could not be verified.'
    }
    $process = Start-Process -FilePath $installerPath -ArgumentList '/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_test=0', 'Include_pip=1', 'Include_tcltk=1', 'Include_launcher=1' -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -notin @(0, 3010)) { throw "Python installer failed with exit code $($process.ExitCode)." }
    exit 0
} catch {
    Write-Host "[PyPack] Python installation failed: $($_.Exception.Message)"
    Write-Host '[PyPack] Install from https://www.python.org/downloads/ and try again.'
    exit 1
} finally {
    if ($downloadDirectory) {
        $resolvedDownload = [IO.Path]::GetFullPath($downloadDirectory)
        $resolvedTemp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
        if ($resolvedDownload.StartsWith($resolvedTemp, [StringComparison]::OrdinalIgnoreCase) -and
            [IO.Path]::GetFileName($resolvedDownload).StartsWith('pypack-python-')) {
            Remove-Item -LiteralPath $resolvedDownload -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}
