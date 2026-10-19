# contracts/lib

The tests need [forge-std](https://github.com/foundry-rs/forge-std) v1.17.0
(commit `f3dae6e6ee381f25eb6a246f7da9b85c91a68219`) at `contracts/lib/forge-std`. It is not committed to this
repository (`.gitignore`). Install it once, from the repository root, in one of these ways:

```
# a plain clone at the pinned tag
git clone --depth 1 --branch v1.17.0 https://github.com/foundry-rs/forge-std contracts/lib/forge-std

# or, with Foundry
cd contracts && forge install foundry-rs/forge-std@v1.17.0 --no-git
```

To keep it as a git submodule instead, remove the `contracts/lib/forge-std` line from `.gitignore` and run
`git submodule add https://github.com/foundry-rs/forge-std contracts/lib/forge-std` followed by
`git -C contracts/lib/forge-std checkout v1.17.0`.

A symbolic link to an existing forge-std v1.17.0 checkout also works:
`ln -s /path/to/forge-std contracts/lib/forge-std`.
