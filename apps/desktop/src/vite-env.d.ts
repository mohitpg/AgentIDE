/// <reference types="vite/client" />

// React 19's types dropped the global JSX namespace (it's React.JSX now). The
// components annotate their return types as JSX.Element, so alias it here.
declare namespace JSX {
  type Element = import('react').JSX.Element
}
