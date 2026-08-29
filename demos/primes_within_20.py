"""打印 20 以内的素数。"""


def is_prime(n: int) -> bool:
    if n < 2:
        return False
    for i in range(2, int(n ** 0.5) + 1):
        if n % i == 0:
            return False
    return True


def main() -> None:
    primes = [n for n in range(2, 20) if is_prime(n)]
    print("20 以内的素数:", primes)


if __name__ == "__main__":
    main()
