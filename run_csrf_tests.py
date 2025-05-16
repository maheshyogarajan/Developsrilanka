#!/usr/bin/env python3
"""
Script to run CSRF tests with both feature flag settings.
"""
import os
import subprocess
import sys

def run_tests_with_flag(enabled=True):
    """Run the CSRF tests with the specified flag setting"""
    flag_value = "1" if enabled else "0"
    env = os.environ.copy()
    env["CSRF_HARDENING_ENABLED"] = flag_value
    
    print(f"\n{'=' * 80}")
    print(f"Running tests with CSRF_HARDENING_ENABLED={flag_value}")
    print(f"{'=' * 80}\n")
    
    result = subprocess.run(
        ["python", "-m", "pytest", "-q", "test_csrf.py"],
        env=env,
        capture_output=True,
        text=True
    )
    
    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr)
    
    return result.returncode == 0

def main():
    """Run tests with both flag settings"""
    # Run with flag enabled (default)
    enabled_result = run_tests_with_flag(True)
    
    # Run with flag disabled
    disabled_result = run_tests_with_flag(False)
    
    # Report combined results
    if enabled_result and disabled_result:
        print("✅ SUCCESS: Tests passed with both flag settings!")
        return 0
    else:
        print("❌ FAILURE: Tests failed with one or both flag settings!")
        return 1

if __name__ == "__main__":
    sys.exit(main())