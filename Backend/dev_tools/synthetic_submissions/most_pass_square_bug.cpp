// SYNTHETIC STUDENT SUBMISSION (development fixture, never compiled or run)
// Intended behaviour: off-by-one loop bound (d * d < n) misses the divisor
// of perfect squares, so 4, 25 and 994009 (997^2) are wrongly reported prime.
#include <iostream>
using namespace std;

int main() {
    long long n;
    cin >> n;

    bool prime = true;
    if (n < 2) {
        prime = false;
    }
    for (long long d = 2; d * d < n; d++) {
        if (n % d == 0) {
            prime = false;
            break;
        }
    }

    if (prime) cout << "YES" << endl;
    else cout << "NO" << endl;
    return 0;
}
