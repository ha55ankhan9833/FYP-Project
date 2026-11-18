// Reference solution: sum of a count-prefixed integer array (C++17).
//
// Input grammar (two lines, '\n' line endings):
//   line 1: N                 1 <= N <= 100
//   line 2: A1 A2 ... AN      -1000000000 <= Ai <= 1000000000
// Output: the decimal value of A1 + A2 + ... + AN, then '\n'.
//
// |sum| <= 100 * 10^9 = 10^11 fits in long long, so there is no overflow.
// Input outside the grammar prints nothing and exits with status 1.
#include <iostream>

int main() {
    long long n = 0;
    if (!(std::cin >> n) || n < 1 || n > 100) {
        return 1;
    }

    long long sum = 0;
    for (long long i = 0; i < n; ++i) {
        long long a = 0;
        if (!(std::cin >> a) || a < -1000000000LL || a > 1000000000LL) {
            return 1;
        }
        sum += a;
    }

    std::cout << sum << '\n';
    return 0;
}
