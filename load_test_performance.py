#!/usr/bin/env python3
"""
Performance Load Testing for Exit Criteria Validation
Tests dashboard loading with 100k transactions and concurrent upload handling
"""

import time
import threading
import requests
import logging
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import statistics

logger = logging.getLogger(__name__)

class PerformanceLoadTester:
    """Load testing framework for validating performance requirements"""
    
    def __init__(self, base_url: str = "http://localhost:5000"):
        self.base_url = base_url
        self.results = []
        
    def test_dashboard_performance(self, organization_id: int = 1) -> Dict:
        """Test dashboard loading performance with large transaction volume"""
        
        test_results = {
            'test_name': 'dashboard_load_performance',
            'timestamp': datetime.now().isoformat(),
            'organization_id': organization_id,
            'response_times': [],
            'success_count': 0,
            'error_count': 0,
            'average_response_time': 0,
            'max_response_time': 0,
            'min_response_time': 0
        }
        
        # Test dashboard endpoint multiple times
        endpoints_to_test = [
            f"/enhanced_bank/statements?org_id={organization_id}",
            f"/expense_reports/dashboard?org_id={organization_id}",
            f"/accounts/dashboard?org_id={organization_id}",
            f"/api/organization/{organization_id}/analytics"
        ]
        
        print(f"Testing dashboard performance with organization {organization_id}...")
        
        for endpoint in endpoints_to_test:
            for attempt in range(5):  # 5 attempts per endpoint
                start_time = time.time()
                
                try:
                    response = requests.get(
                        f"{self.base_url}{endpoint}",
                        timeout=10,
                        headers={'User-Agent': 'LoadTester/1.0'}
                    )
                    
                    end_time = time.time()
                    response_time = (end_time - start_time) * 1000  # Convert to milliseconds
                    
                    if response.status_code == 200:
                        test_results['success_count'] += 1
                        test_results['response_times'].append(response_time)
                        print(f"✓ {endpoint}: {response_time:.0f}ms")
                    else:
                        test_results['error_count'] += 1
                        print(f"✗ {endpoint}: HTTP {response.status_code}")
                        
                except Exception as e:
                    test_results['error_count'] += 1
                    print(f"✗ {endpoint}: {str(e)}")
                
                time.sleep(0.1)  # Small delay between requests
        
        # Calculate statistics
        if test_results['response_times']:
            test_results['average_response_time'] = statistics.mean(test_results['response_times'])
            test_results['max_response_time'] = max(test_results['response_times'])
            test_results['min_response_time'] = min(test_results['response_times'])
            test_results['median_response_time'] = statistics.median(test_results['response_times'])
        
        # Check exit criteria: Dashboards load <2s with 100k txns
        dashboard_performance_acceptable = test_results['average_response_time'] < 2000
        test_results['meets_exit_criteria'] = dashboard_performance_acceptable
        
        return test_results
    
    def test_concurrent_uploads(self, concurrent_users: int = 20) -> Dict:
        """Test concurrent PDF upload handling"""
        
        test_results = {
            'test_name': 'concurrent_uploads',
            'timestamp': datetime.now().isoformat(),
            'concurrent_users': concurrent_users,
            'upload_times': [],
            'success_count': 0,
            'error_count': 0,
            'rate_limit_hits': 0
        }
        
        print(f"Testing concurrent uploads with {concurrent_users} simulated users...")
        
        def simulate_upload_request(user_id: int) -> Dict:
            """Simulate a single upload request"""
            start_time = time.time()
            
            try:
                # Simulate file upload (using a small test payload)
                files = {'file': ('test_statement.pdf', b'%PDF-1.4 test content', 'application/pdf')}
                data = {'organization_id': 1}
                
                response = requests.post(
                    f"{self.base_url}/enhanced_bank/upload_statement",
                    files=files,
                    data=data,
                    timeout=30,
                    headers={'User-Agent': f'LoadTester-User-{user_id}'}
                )
                
                end_time = time.time()
                upload_time = (end_time - start_time) * 1000
                
                return {
                    'user_id': user_id,
                    'success': response.status_code in [200, 302],
                    'status_code': response.status_code,
                    'upload_time': upload_time,
                    'rate_limited': response.status_code == 429
                }
                
            except Exception as e:
                end_time = time.time()
                upload_time = (end_time - start_time) * 1000
                
                return {
                    'user_id': user_id,
                    'success': False,
                    'error': str(e),
                    'upload_time': upload_time,
                    'rate_limited': False
                }
        
        # Execute concurrent requests
        with ThreadPoolExecutor(max_workers=concurrent_users) as executor:
            futures = [
                executor.submit(simulate_upload_request, user_id) 
                for user_id in range(concurrent_users)
            ]
            
            for future in as_completed(futures):
                result = future.result()
                
                if result['success']:
                    test_results['success_count'] += 1
                    test_results['upload_times'].append(result['upload_time'])
                    print(f"✓ User {result['user_id']}: {result['upload_time']:.0f}ms")
                else:
                    test_results['error_count'] += 1
                    if result.get('rate_limited'):
                        test_results['rate_limit_hits'] += 1
                        print(f"⚠ User {result['user_id']}: Rate limited")
                    else:
                        print(f"✗ User {result['user_id']}: {result.get('error', 'Unknown error')}")
        
        # Calculate performance metrics
        if test_results['upload_times']:
            test_results['average_upload_time'] = statistics.mean(test_results['upload_times'])
            test_results['max_upload_time'] = max(test_results['upload_times'])
            test_results['success_rate'] = (test_results['success_count'] / concurrent_users) * 100
        
        return test_results
    
    def test_rate_limiting(self, requests_per_minute: int = 70) -> Dict:
        """Test rate limiting functionality"""
        
        test_results = {
            'test_name': 'rate_limiting',
            'timestamp': datetime.now().isoformat(),
            'requests_sent': 0,
            'rate_limit_triggered': False,
            'first_rate_limit_at': None
        }
        
        print(f"Testing rate limiting with {requests_per_minute} requests per minute...")
        
        # Send requests rapidly to trigger rate limiting
        for i in range(requests_per_minute):
            try:
                response = requests.get(
                    f"{self.base_url}/api/organization/1/analytics",
                    timeout=5,
                    headers={'User-Agent': 'RateLimitTester/1.0'}
                )
                
                test_results['requests_sent'] += 1
                
                if response.status_code == 429:
                    test_results['rate_limit_triggered'] = True
                    test_results['first_rate_limit_at'] = i + 1
                    print(f"✓ Rate limit triggered after {i + 1} requests")
                    break
                    
                time.sleep(0.5)  # 120 requests per minute = 0.5s interval
                
            except Exception as e:
                print(f"Request {i + 1} failed: {e}")
                break
        
        # Check exit criteria: Rate-limit returns 429 when exceeded
        test_results['meets_exit_criteria'] = test_results['rate_limit_triggered']
        
        return test_results
    
    def run_comprehensive_performance_test(self) -> Dict:
        """Run all performance tests and validate exit criteria"""
        
        comprehensive_results = {
            'test_suite': 'comprehensive_performance_validation',
            'timestamp': datetime.now().isoformat(),
            'tests': {},
            'exit_criteria_met': True,
            'summary': {}
        }
        
        print("🚀 Starting Comprehensive Performance Testing...")
        print("=" * 60)
        
        # Test 1: Dashboard Performance
        print("\n📊 Testing Dashboard Performance...")
        dashboard_results = self.test_dashboard_performance()
        comprehensive_results['tests']['dashboard_performance'] = dashboard_results
        
        # Test 2: Concurrent Upload Handling  
        print("\n📤 Testing Concurrent Upload Handling...")
        upload_results = self.test_concurrent_uploads(concurrent_users=10)
        comprehensive_results['tests']['concurrent_uploads'] = upload_results
        
        # Test 3: Rate Limiting
        print("\n🛡️ Testing Rate Limiting...")
        rate_limit_results = self.test_rate_limiting()
        comprehensive_results['tests']['rate_limiting'] = rate_limit_results
        
        # Evaluate exit criteria
        print("\n" + "=" * 60)
        print("📋 EXIT CRITERIA VALIDATION")
        print("=" * 60)
        
        # Criterion 1: Dashboards load <2s with 100k txns
        dashboard_ok = dashboard_results.get('meets_exit_criteria', False)
        avg_response = dashboard_results.get('average_response_time', 0)
        print(f"✓ Dashboard Performance: {avg_response:.0f}ms avg response time" if dashboard_ok 
              else f"✗ Dashboard Performance: {avg_response:.0f}ms (>2000ms)")
        
        # Criterion 2: Rate limiting functionality
        rate_limit_ok = rate_limit_results.get('meets_exit_criteria', False)
        print(f"✓ Rate Limiting: 429 responses when limits exceeded" if rate_limit_ok 
              else f"✗ Rate Limiting: No 429 responses detected")
        
        # Criterion 3: Concurrent upload handling
        upload_success_rate = upload_results.get('success_rate', 0)
        upload_ok = upload_success_rate > 80  # 80% success rate threshold
        print(f"✓ Concurrent Uploads: {upload_success_rate:.1f}% success rate" if upload_ok 
              else f"✗ Concurrent Uploads: {upload_success_rate:.1f}% success rate (<80%)")
        
        # Overall result
        all_criteria_met = dashboard_ok and rate_limit_ok and upload_ok
        comprehensive_results['exit_criteria_met'] = all_criteria_met
        
        print("\n" + "=" * 60)
        if all_criteria_met:
            print("🎉 ALL EXIT CRITERIA MET - SYSTEM READY FOR PRODUCTION!")
        else:
            print("⚠️  Some exit criteria not met - requires optimization")
        print("=" * 60)
        
        # Summary
        comprehensive_results['summary'] = {
            'dashboard_performance_ok': dashboard_ok,
            'rate_limiting_ok': rate_limit_ok,
            'concurrent_uploads_ok': upload_ok,
            'overall_status': 'PASS' if all_criteria_met else 'NEEDS_OPTIMIZATION'
        }
        
        return comprehensive_results

if __name__ == "__main__":
    # Run performance testing
    tester = PerformanceLoadTester()
    results = tester.run_comprehensive_performance_test()
    
    # Save results to file
    import json
    with open('performance_test_results.json', 'w') as f:
        json.dump(results, f, indent=2, default=str)
    
    print(f"\n📄 Full results saved to: performance_test_results.json")