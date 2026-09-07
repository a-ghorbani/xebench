/**
 * @format
 */

import React from 'react';
import ReactTestRenderer from 'react-test-renderer';
import App from '../App';
import {runReference} from '../src/harness/reference';

jest.mock('../src/harness/reference', () => ({runReference: jest.fn(async () => {})}));
jest.useFakeTimers();

test('auto-runs the reference configuration and emits completion', async () => {
  const consoleLog = jest.spyOn(console, 'log').mockImplementation(() => {});
  let tree: ReactTestRenderer.ReactTestRenderer;
  await ReactTestRenderer.act(async () => {
    tree = ReactTestRenderer.create(<App />);
  });
  await ReactTestRenderer.act(async () => {jest.advanceTimersByTime(800);});
  expect(runReference).toHaveBeenCalledTimes(1);
  expect(consoleLog).toHaveBeenCalledWith('XEBENCH_DONE');
  await ReactTestRenderer.act(async () => {tree.unmount();});
  consoleLog.mockRestore();
});
