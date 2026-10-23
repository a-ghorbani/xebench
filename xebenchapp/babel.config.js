module.exports = {
  presets: ['module:@react-native/babel-preset'],
  // react-native-executorch runs its native calls in worklets (react-native-worklets).
  // The worklets plugin must be the last plugin.
  plugins: ['react-native-worklets/plugin'],
};
