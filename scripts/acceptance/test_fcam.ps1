# FCAM 最小测试用例 - Windows PowerShell
# 用法: .\scripts\acceptance\test_fcam.ps1

$FCAM_URL = "http://127.0.0.1:8000"

Write-Host "=== FCAM 接口测试 ===" -ForegroundColor Cyan
Write-Host ""

# 测试 1: 探活检查（不需要认证）
Write-Host "[1] 测试探活接口 /healthz ..." -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "$FCAM_URL/healthz" -Method GET -UseBasicParsing
    Write-Host "✓ 探活成功: $($response.StatusCode)" -ForegroundColor Green
    Write-Host "  响应: $($response.Content)" -ForegroundColor Gray
} catch {
    Write-Host "✗ 探活失败: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host ""

# 测试 2: 就绪检查（检查配置和数据库）
Write-Host "[2] 测试就绪接口 /readyz ..." -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "$FCAM_URL/readyz" -Method GET -UseBasicParsing
    Write-Host "✓ 就绪检查成功: $($response.StatusCode)" -ForegroundColor Green
    Write-Host "  响应: $($response.Content)" -ForegroundColor Gray
} catch {
    Write-Host "✗ 就绪检查失败: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "  提示: 检查 FCAM_ADMIN_TOKEN 和 FCAM_MASTER_KEY 环境变量是否配置" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== 测试完成 ===" -ForegroundColor Cyan

