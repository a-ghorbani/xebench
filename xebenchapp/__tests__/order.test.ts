import {generateSeed, seededOrder} from '../src/harness/order';

const ids = ['a', 'b', 'c', 'd', 'e'];

test('a recorded seed reproduces the same permutation', () => {
  const order = seededOrder(ids, 42);
  expect(seededOrder(ids, 42)).toEqual(order);
  expect([...order].sort()).toEqual(ids);
  expect(ids).toEqual(['a', 'b', 'c', 'd', 'e']);
  // Pinned so an implementation change cannot silently reinterpret recorded seeds.
  expect(order.join('')).toBe('aecbd');
});

test('different seeds explore different orders', () => {
  const orders = new Set(Array.from({length: 50}, (_, seed) => seededOrder(ids, seed).join('')));
  expect(orders.size).toBeGreaterThan(20);
  expect(seededOrder([], 1)).toEqual([]);
});

test('generated seeds are unsigned 32-bit integers', () => {
  for (let index = 0; index < 100; index++) {
    const seed = generateSeed();
    expect(Number.isInteger(seed) && seed >= 0 && seed <= 0xffffffff).toBe(true);
  }
});
