# Install uv for yanhekit when it is missing, and print the path to write in place of `uv` until the harness restarts.
$ErrorActionPreference = 'Stop'
# A PowerShell 7 harness passes its module path down, and Windows PowerShell cannot load its own modules from it
$env:PSModulePath = [Environment]::GetEnvironmentVariable('PSModulePath', 'Machine')
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 'Tls12'

$found = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if ($found) {
  "uv is ready: $($found.Source)"
  exit 0
}

$dir = if ($env:UV_INSTALL_DIR) { $env:UV_INSTALL_DIR } else { Join-Path $HOME '.local\bin' }
$uv = Join-Path $dir 'uv.exe'
if (-not (Test-Path $uv)) {
  [Console]::Error.WriteLine('Downloading uv, about 15 MB; on a slow connection this takes a few minutes.')
  foreach ($url in 'https://astral.sh/uv/install.ps1', 'https://github.com/astral-sh/uv/releases/latest/download/uv-installer.ps1') {
    try {
      $installer = Invoke-RestMethod $url -TimeoutSec 60
      $env:UV_INSTALL_DIR = $dir
      & ([scriptblock]::Create($installer)) *>&1 | ForEach-Object { [Console]::Error.WriteLine($_) }
      break
    } catch {
      [Console]::Error.WriteLine("Could not install from ${url}: $($_.Exception.Message)")
    }
  }
}
if (-not (Test-Path $uv)) {
  [Console]::Error.WriteLine('Could not download uv. In a sandbox, run this script again with network access; otherwise see https://docs.astral.sh/uv/getting-started/installation/')
  exit 1
}
$path = $uv.Replace('\', '/')
"uv installed: $path"
"New sessions find it as uv; until the harness restarts, write $path in place of uv."
