param(
  [Parameter(Mandatory=$true)][ValidateSet('post-edit','stop')][string]$Event,
  [string]$Root = (Get-Location).Path,
  [string]$File = ''
)

$pythonCandidates = @(
  $env:CODEX_PYTHON,
  'C:\Users\DTL\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe',
  'python.exe',
  'py.exe'
) | Where-Object { $_ }

$python = $pythonCandidates | Where-Object {
  if ($_ -match '[\\/]') { Test-Path -LiteralPath $_ } else { Get-Command $_ -ErrorAction SilentlyContinue }
} | Select-Object -First 1

if (-not $python) {
  Write-Error 'Không tìm thấy Python cho Codex gate. Đặt CODEX_PYTHON hoặc cài Python.'
  exit 2
}

$adapter = Join-Path $PSScriptRoot 'codex_gate.py'
$arguments = @($adapter, $Event, '--root', $Root)
if ($Event -eq 'post-edit') { $arguments += @('--file', $File) }
& $python @arguments
exit $LASTEXITCODE
