// SYNTHETIC E2E SUBMISSION - Assessment 6 automatic-evaluation demo.
// Developer-written and reviewed; deliberately accumulates in a 32-bit int,
// so sums beyond the int range (e.g. 5 x 1000000000) overflow.
#include <iostream>

int main() {
    int n = 0;
    std::cin >> n;
    int sum = 0;
    for (int i = 0; i < n; ++i) {
        int value = 0;
        std::cin >> value;
        sum += value;
    }
    std::cout << sum << '\n';
    return 0;
}
