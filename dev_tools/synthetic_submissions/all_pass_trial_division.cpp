// SYNTHETIC STUDENT SUBMISSION (development fixture, never compiled or run)
// Intended behaviour: correct for every valid N.
#include <iostream>

bool isPrime(long long n) {
    if (n < 2) {
        return false;
    }
    for (long long d = 2; d * d <= n; ++d) {
        if (n % d == 0) {
            return false;
        }
    }
    return true;
}

int main() {
    long long n;
    std::cin >> n;
    std::cout << (isPrime(n) ? "YES" : "NO") << '\n';
    return 0;
}
