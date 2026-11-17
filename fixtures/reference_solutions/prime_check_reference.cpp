#include <iostream>

int main() {
    long long n;
    if (!(std::cin >> n)) {
        return 0;
    }

    bool prime = n >= 2;
    for (long long d = 2; prime && d * d <= n; ++d) {
        if (n % d == 0) {
            prime = false;
        }
    }

    std::cout << (prime ? "YES" : "NO") << '\n';
    return 0;
}
