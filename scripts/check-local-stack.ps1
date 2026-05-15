param(
  [string]$ApiBaseUrl = "http://127.0.0.1:8000",
  [string]$FrontendBaseUrl = "http://127.0.0.1:3000"
)

$ErrorActionPreference = "Stop"

function Test-Endpoint {
  param(
    [string]$Name,
    [string]$Url
  )

  try {
    $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 8
    Write-Host "$Name OK $($response.StatusCode)"
    return $true
  } catch {
    $status = $_.Exception.Response.StatusCode.value__
    if ($status) {
      Write-Host "$Name FAILED $status"
    } else {
      Write-Host "$Name FAILED $($_.Exception.Message)"
    }
    return $false
  }
}

$checks = @(
  (Test-Endpoint "frontend_dashboard" "$FrontendBaseUrl/dashboard"),
  (Test-Endpoint "api_calls" "$ApiBaseUrl/internal/v1/calls?limit=1"),
  (Test-Endpoint "api_bookings" "$ApiBaseUrl/internal/v1/bookings?limit=1"),
  (Test-Endpoint "api_settings" "$ApiBaseUrl/internal/v1/settings/business")
)

if ($checks -contains $false) {
  exit 1
}
