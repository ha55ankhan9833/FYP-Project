// SYNTHETIC STUDENT SUBMISSION (development fixture, never compiled or run)
// Intended behaviour: primality logic is correct, but it prints "Yes"/"No"
// instead of the required case-sensitive "YES"/"NO", so every test fails.
#include <iostream>

int main() {
    long long n;
    std::cin >> n;

    bool prime = n >= 2;
    for (long long d = 2; prime && d * d <= n; ++d) {
        if (n % d == 0) {
            prime = false;
        }
    }

    std::cout << (prime ? "Yes" : "No") << '\n';
    return 0;
}
