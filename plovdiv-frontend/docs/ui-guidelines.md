# UI guidelines

Conventions for the SolidJS and Vite frontend. They exist to keep the interface consistent, accessible and easy to maintain.

## 1. Components

- Keep components small, with one logical component per file.
- Separate data fetching (queries and resources) from presentational components. Presentational components receive data through props.

## 2. Styling

- Use Tailwind CSS utilities. Do not add inline `style` attributes (enforced by ESLint). Custom CSS lives in `src/index.css` only: the colour tokens, the `@theme inline` mapping and the vendor chart tweaks. Vendor stylesheets imported from `node_modules` (for example the chart library's) are the other exception.
- Use the semantic colour utilities (`bg-surface`, `text-ink`, `border-rule`, `text-primary`) rather than raw palette colours. They resolve to CSS variables, so light and dark themes switch in one place. Colour never carries meaning alone; pair it with a label, icon or pattern.
- Use a consistent spacing scale (`p-4`, `m-2`).
- Prefer standard utilities over arbitrary values (`w-80` rather than `w-[321px]`).

## 3. Layout

- Build layouts with Flexbox or CSS Grid. Do not use floats or absolute positioning for structure.
- Layouts must work on small screens, using responsive prefixes (`flex-col md:flex-row`).

## 4. Accessibility and semantics

- Use semantic HTML: `nav`, `header`, `main`, `article`, `button`.
- Every button has visible text or an `aria-label`. Check colour contrast.

## 5. State

- Use `createSignal`, `createMemo` and `createResource` for what they are meant for. Derive values with `createMemo` instead of storing them in extra signals.
