# Local Vendor Assets

This folder contains third-party assets mirrored locally so the app can load without CDN access.

## JavaScript

- `js/tailwindcss-cdn.js`: legacy local mirror retained for old package compatibility; current pages do not load it.
- `js/lucide.js`: Lucide `1.16.0` (ISC).
- `js/three-0.160.0.module.js`: local mirror of `https://unpkg.com/three@0.160.0/build/three.module.js`

## CSS

- `css/fonts.css`: local `@font-face` declarations used by all pages.
- `css/tailwind.css`: precompiled from Tailwind CSS `3.4.17` (MIT) using the locked root npm dependency.

## Fonts

- `fonts/inter-5.ttf`: Inter 300
- `fonts/inter-4.ttf`: Inter 400
- `fonts/inter-3.ttf`: Inter 500
- `fonts/inter-2.ttf`: Inter 600
- `fonts/inter-1.ttf`: Inter 800
- `fonts/jetbrains-mono-7.ttf`: JetBrains Mono 400
- `fonts/jetbrains-mono-6.ttf`: JetBrains Mono 700
- `fonts/space-grotesk-9.ttf`: Space Grotesk 300
- `fonts/space-grotesk-10.ttf`: Space Grotesk 500
- `fonts/space-grotesk-8.ttf`: Space Grotesk 700
