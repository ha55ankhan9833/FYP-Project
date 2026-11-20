// SYNTHETIC E2E SUBMISSION (W3) - written for the FYP_E2E_Test demo only.
// Submitted as student E2E-TEST-0001 to Assessment 6 and graded MANUALLY.
// It is never compiled or executed by the backend or by the E2E driver.
#include <iostream>

int main() {
    int n = 0;
    std::cin >> n;
    long long sum = 0;
    for (int i = 0; i < n; ++i) {
        long long value = 0;
        std::cin >> value;
        sum += value;
    }
    std::cout << sum << '\n';
    return 0;
}
