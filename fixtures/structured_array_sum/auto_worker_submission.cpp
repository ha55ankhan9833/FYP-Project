// SYNTHETIC E2E SUBMISSION - automatic LLM-worker demo (FYP_E2E_Test only).
// Submitted as student E2E-TEST-0004 to Assessment 6. It is analysed by the
// offline LLM worker from its source code only: never compiled or executed.
#include <iostream>

int main() {
    int count = 0;
    std::cin >> count;
    long long total = 0;
    for (int i = 0; i < count; ++i) {
        long long value = 0;
        std::cin >> value;
        total += value;
    }
    std::cout << total << '\n';
    return 0;
}
