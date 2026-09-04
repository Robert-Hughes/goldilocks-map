// proj4's type declarations reference its optional geotiff integration even though
// Goldilocks does not use that feature. Keep strict type-checking independent of
// the unused optional runtime dependency.
declare module "geotiff";
