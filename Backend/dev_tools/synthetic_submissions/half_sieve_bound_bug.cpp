// SYNTHETIC STUDENT SUBMISSION (development fixture, never compiled or run)
// Intended behaviour: the sieve's inner loop stops at 1000 instead of MAXN,
// so composites above 1000 are never crossed out and print YES.
#include <iostream>
#include <vector>
using namespace std;

const int MAXN = 1000000;

int main() {
    vector<bool> isPrime(MAXN + 1, true);
    isPrime[0] = false;
    isPrime[1] = false;
    for (int i = 2; i * i <= MAXN; i++) {
        if (isPrime[i]) {
            for (int j = i * i; j <= 1000; j += i) {
                isPrime[j] = false;
            }
        }
    }

    int n;
    cin >> n;
    cout << (isPrime[n] ? "YES" : "NO") << "\n";
    return 0;
}
