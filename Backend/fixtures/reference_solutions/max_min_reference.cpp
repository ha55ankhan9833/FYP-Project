// Reference solution: maximum and minimum of a count-prefixed integer array (C++17).
//
// Input grammar (two lines, '\n' line endings):
//   line 1: N                 1 <= N <= 100
//   line 2: A1 A2 ... AN      -1000 <= Ai <= 1000
// Output: the maximum element, '\n', the minimum element, '\n'.
//
// Follows the assignment rules: a for loop, if/else comparisons, no sort(),
// no recursion, cin/cout. Values fit in int, so there is no overflow.
// Input outside the grammar prints nothing and exits with status 1.
#include <iostream>

int main() {
    int n = 0;
    if (!(std::cin >> n) || n < 1 || n > 100) {
        return 1;
    }

    int maximum = 0;
    int minimum = 0;
    for (int i = 0; i < n; ++i) {
        int a = 0;
        if (!(std::cin >> a) || a < -1000 || a > 1000) {
            return 1;
        }
        if (i == 0) {
            maximum = a;
            minimum = a;
        } else {
            if (a > maximum) {
                maximum = a;
            }
            if (a < minimum) {
                minimum = a;
            }
        }
    }

    std::cout << maximum << '\n' << minimum << '\n';
    return 0;
}
