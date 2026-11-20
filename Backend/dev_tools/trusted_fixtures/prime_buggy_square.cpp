#include <iostream>

int main() {
    long long n = 0;
    if (!(std::cin >> n)) {
        return 1;
    }
    bool prime = n >= 2;
    for (long long d = 2; d * d < n; ++d) {
        if (n % d == 0) {
            prime = false;
            break;
        }
    }
    std::cout << (prime ? "YES" : "NO") << '\n';
    return 0;
}
