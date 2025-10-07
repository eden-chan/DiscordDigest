param(
  [int]$Port = 5555
)

Write-Host "[kill_port] Checking listeners on port $Port ..."
try {
  $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
  $pids = $conns | Select-Object -Expand OwningProcess -Unique
  if ($pids) {
    foreach ($pid in $pids) {
      Write-Host "[kill_port] Killing PID $pid"
      Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
    }
    Write-Host "[kill_port] Done."
  } else {
    Write-Host "[kill_port] No listeners found."
  }
} catch {
  Write-Host "[kill_port] Failed to query listeners via Get-NetTCPConnection. Trying netstat..."
  $lines = netstat -ano | Select-String ":$Port\s"
  if ($lines) {
    $pids = @()
    foreach ($line in $lines) {
      $parts = ($line -split "\s+") | Where-Object { $_ -ne '' }
      if ($parts.Length -ge 5) { $pids += $parts[-1] }
    }
    $pids = $pids | Select-Object -Unique
    foreach ($pid in $pids) {
      Write-Host "[kill_port] Killing PID $pid"
      Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
    }
    Write-Host "[kill_port] Done."
  } else {
    Write-Host "[kill_port] No listeners found."
  }
}

