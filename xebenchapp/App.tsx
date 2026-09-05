import React, {useCallback, useEffect, useRef, useState} from 'react';
import {SafeAreaView, ScrollView, Text, Pressable, StyleSheet} from 'react-native';
import {runReference} from './src/harness/reference';

export default function App() {
  const [logs, setLogs] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const running = useRef(false);
  const log = useCallback((message: string) => setLogs(previous => [...previous, message]), []);

  const run = useCallback(async () => {
    if (running.current) {
      return;
    }
    running.current = true;
    setBusy(true);
    try {
      await runReference(log);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      log(message);
      console.log(`XEBENCH_ERROR reference: ${message}`);
    } finally {
      console.log('XEBENCH_DONE');
      running.current = false;
      setBusy(false);
    }
  }, [log]);

  useEffect(() => {
    const timer = setTimeout(run, 800);
    return () => clearTimeout(timer);
  }, [run]);

  return (
    <SafeAreaView style={styles.root}>
      <Text style={styles.title}>xebench CPU reference</Text>
      <Pressable accessibilityRole="button" onPress={run} disabled={busy} style={styles.button}>
        <Text>{busy ? 'Running…' : 'Run configuration'}</Text>
      </Pressable>
      <ScrollView>
        {logs.map((message, index) => <Text key={index} style={styles.log}>{message}</Text>)}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: {flex: 1, backgroundColor: '#111', padding: 12},
  title: {color: '#fff', fontSize: 20, marginBottom: 12},
  button: {backgroundColor: '#8ab4f8', padding: 12, marginBottom: 12},
  log: {color: '#ddd', fontFamily: 'monospace', marginBottom: 4},
});
