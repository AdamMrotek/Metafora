# `shared-ui` — one product, three apps

Design tokens and components, so the three front ends look like one product.
Browser only; no service imports it.

Today it is `tokens.css` and nothing else, published as `@metafora/ui` and
imported by `frontend/call`. Components move here the moment a second app needs
one.
