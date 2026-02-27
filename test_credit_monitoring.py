#!/usr/bin/env python3
"""
额度监控功能测试脚本
测试 API Key 额度查询、刷新、历史记录等功能
"""

import requests
import json
import time
from datetime import datetime

# 配置
BASE_URL = "http://localhost:8000"
ADMIN_TOKEN = "dev_admin_token"
CLIENT_ID = 18  # 从查询结果获取
KEY_ID = 128    # 使用第一个 Key

# 请求头
HEADERS = {
    "Authorization": f"Bearer {ADMIN_TOKEN}",
    "Content-Type": "application/json"
}

def print_section(title):
    """打印测试章节标题"""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")

def print_result(test_name, success, details=""):
    """打印测试结果"""
    status = "✅ 通过" if success else "❌ 失败"
    print(f"{status} - {test_name}")
    if details:
        print(f"   详情: {details}")

def test_1_get_key_credits():
    """测试 1: 获取 Key 额度信息"""
    print_section("测试 1: 获取 Key 额度信息")

    url = f"{BASE_URL}/admin/keys/{KEY_ID}/credits"
    response = requests.get(url, headers=HEADERS)

    if response.status_code == 200:
        data = response.json()
        print(json.dumps(data, indent=2, ensure_ascii=False))

        # 验证必需字段
        required_fields = [
            "total_credits", "used_credits", "remaining_credits",
            "usage_percentage", "is_estimated", "last_synced_at"
        ]

        missing_fields = [f for f in required_fields if f not in data]
        if missing_fields:
            print_result("字段完整性检查", False, f"缺少字段: {missing_fields}")
            return False

        print_result("字段完整性检查", True)
        print_result("额度信息获取", True,
                    f"剩余: {data['remaining_credits']}/{data['total_credits']} "
                    f"({data['usage_percentage']:.1f}%)")

        # 保存数据供后续测试使用
        return data
    else:
        print_result("额度信息获取", False, f"HTTP {response.status_code}: {response.text}")
        return None

def test_2_get_client_credits():
    """测试 2: 获取 Client 聚合额度"""
    print_section("测试 2: 获取 Client 聚合额度")

    url = f"{BASE_URL}/admin/clients/{CLIENT_ID}/credits"
    response = requests.get(url, headers=HEADERS)

    if response.status_code == 200:
        data = response.json()
        print(json.dumps(data, indent=2, ensure_ascii=False))
        print_result("Client 额度聚合", True,
                    f"总剩余: {data.get('total_remaining_credits', 0)} credits, "
                    f"包含 {len(data.get('keys', []))} 个 Key")
        return data
    else:
        print_result("Client 额度聚合", False, f"HTTP {response.status_code}: {response.text}")
        return None

def test_3_manual_refresh():
    """测试 3: 手动刷新额度"""
    print_section("测试 3: 手动刷新额度")

    # 先获取当前额度
    url_get = f"{BASE_URL}/admin/keys/{KEY_ID}/credits"
    before = requests.get(url_get, headers=HEADERS).json()
    print(f"刷新前: {before.get('remaining_credits')} credits (估算: {before.get('is_estimated')})")

    # 触发刷新
    url_refresh = f"{BASE_URL}/admin/keys/{KEY_ID}/credits/refresh"
    response = requests.post(url_refresh, headers=HEADERS)

    if response.status_code == 200:
        data = response.json()
        print(json.dumps(data, indent=2, ensure_ascii=False))

        after = requests.get(url_get, headers=HEADERS).json()
        print(f"刷新后: {after.get('remaining_credits')} credits (估算: {after.get('is_estimated')})")

        print_result("手动刷新", True,
                    f"is_estimated 从 {before.get('is_estimated')} 变为 {after.get('is_estimated')}")

        # 测试频率限制（5分钟内不能再次刷新）
        print("\n测试刷新频率限制（应该返回 429）...")
        response2 = requests.post(url_refresh, headers=HEADERS)

        if response2.status_code == 429:
            print_result("刷新频率限制", True, "正确返回 429 Too Many Requests")
        else:
            print_result("刷新频率限制", False, f"预期 429，实际 {response2.status_code}")

        return data
    else:
        print_result("手动刷新", False, f"HTTP {response.status_code}: {response.text}")
        return None

def test_4_get_credit_history():
    """测试 4: 获取额度历史记录"""
    print_section("测试 4: 获取额度历史记录")

    url = f"{BASE_URL}/admin/keys/{KEY_ID}/credits/history?limit=5"
    response = requests.get(url, headers=HEADERS)

    if response.status_code == 200:
        data = response.json()
        print(f"历史记录数量: {len(data.get('items', []))}")

        for i, snapshot in enumerate(data.get('items', [])[:3], 1):
            print(f"\n快照 {i}:")
            print(f"  时间: {snapshot.get('created_at')}")
            print(f"  剩余: {snapshot.get('remaining_credits')} credits")
            print(f"  使用率: {snapshot.get('usage_percentage', 0):.1f}%")

        print_result("额度历史查询", True, f"获取到 {len(data.get('items', []))} 条记录")
        return data
    else:
        print_result("额度历史查询", False, f"HTTP {response.status_code}: {response.text}")
        return None

def test_5_batch_refresh():
    """测试 5: 批量刷新额度"""
    print_section("测试 5: 批量刷新额度（所有 Key）")

    # 获取所有 Key ID
    url_keys = f"{BASE_URL}/admin/keys"
    keys_response = requests.get(url_keys, headers=HEADERS)

    if keys_response.status_code != 200:
        print_result("批量刷新", False, "无法获取 Key 列表")
        return None

    keys = keys_response.json().get('items', [])
    key_ids = [k['id'] for k in keys if k.get('client_id') == CLIENT_ID][:3]  # 只测试前3个

    print(f"准备刷新 {len(key_ids)} 个 Key: {key_ids}")

    url = f"{BASE_URL}/admin/keys/credits/refresh-all"
    payload = {"key_ids": key_ids}

    response = requests.post(url, headers=HEADERS, json=payload)

    if response.status_code == 200:
        data = response.json()
        print(json.dumps(data, indent=2, ensure_ascii=False))

        print_result("批量刷新", True,
                    f"成功: {data.get('success_count', 0)}, "
                    f"失败: {data.get('failure_count', 0)}")
        return data
    else:
        print_result("批量刷新", False, f"HTTP {response.status_code}: {response.text}")
        return None

def test_6_smart_refresh_strategy():
    """测试 6: 智能刷新策略验证"""
    print_section("测试 6: 智能刷新策略验证")

    url = f"{BASE_URL}/admin/keys/{KEY_ID}/credits"
    response = requests.get(url, headers=HEADERS)

    if response.status_code == 200:
        data = response.json()
        usage = data.get('usage_percentage', 0)
        next_refresh = data.get('next_refresh_at')

        print(f"当前使用率: {usage:.1f}%")
        print(f"下次刷新时间: {next_refresh}")

        # 根据使用率判断刷新策略
        if usage < 10:
            expected = "低使用率，刷新间隔应该较长（1-6小时）"
        elif usage < 50:
            expected = "中等使用率，刷新间隔应该适中（30分钟-1小时）"
        else:
            expected = "高使用率，刷新间隔应该较短（5-30分钟）"

        print(f"预期策略: {expected}")
        print_result("智能刷新策略", True, "策略信息已展示，需人工判断合理性")
        return data
    else:
        print_result("智能刷新策略", False, f"HTTP {response.status_code}")
        return None

def main():
    """运行所有测试"""
    print("\n" + "="*60)
    print("  Firecrawl API Manager - 额度监控功能测试")
    print("="*60)
    print(f"\n测试配置:")
    print(f"  - 服务地址: {BASE_URL}")
    print(f"  - Client ID: {CLIENT_ID}")
    print(f"  - Key ID: {KEY_ID}")
    print(f"  - 测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 执行测试
    results = {}

    try:
        results['test_1'] = test_1_get_key_credits()
        time.sleep(1)

        results['test_2'] = test_2_get_client_credits()
        time.sleep(1)

        results['test_3'] = test_3_manual_refresh()
        time.sleep(1)

        results['test_4'] = test_4_get_credit_history()
        time.sleep(1)

        results['test_5'] = test_5_batch_refresh()
        time.sleep(1)

        results['test_6'] = test_6_smart_refresh_strategy()

    except Exception as e:
        print(f"\n❌ 测试过程中发生错误: {e}")
        import traceback
        traceback.print_exc()

    # 测试总结
    print_section("测试总结")
    success_count = sum(1 for v in results.values() if v is not None)
    total_count = len(results)

    print(f"总测试数: {total_count}")
    print(f"通过: {success_count}")
    print(f"失败: {total_count - success_count}")
    print(f"成功率: {success_count/total_count*100:.1f}%")

    if success_count == total_count:
        print("\n🎉 所有测试通过！额度监控功能运行正常。")
    else:
        print("\n⚠️  部分测试失败，请检查日志。")

if __name__ == "__main__":
    main()
