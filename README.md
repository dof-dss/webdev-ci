# Web development CI config

This repo houses the shared CI config for most webdev repositories.

It allows us to more easily maintain common elements of CI config across our projects. Pipelines can now be given parameters, per project, which allows for localised overrides or sensible defaults to permit variation across projects.

## How it works

Circle CI dynamic pipelines (https://circleci.com/docs/using-dynamic-configuration/#a-basic-example) with Circle CI orbs (https://circleci.com/docs/orb-intro/) for preprocessing our config.

## Gotchas

- YAML anchors can't be preprocessed with pipeline parameters (yet), so you'll see these re-declared to prioritise parameter usage over lack of repetition.
- There's a preprocess/setup step involved now where a continuation orb will take the central config file, and preprocess it into a final YAML file which is then executed in the usual workflow steps.

## Licence

Unless stated otherwise, the codebase is released under the [MIT License](http://www.opensource.org/licenses/mit-license.php). This covers both the codebase and any sample code in the documentation.