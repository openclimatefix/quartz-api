# Quartz API
<!-- ALL-CONTRIBUTORS-BADGE:START - Do not remove or modify this section -->
[![All Contributors](https://img.shields.io/badge/all_contributors-8-orange.svg?style=flat-square)](#contributors-)
<!-- ALL-CONTRIBUTORS-BADGE:END -->

API providing external access to Quartz forecast data.

## Running the service

### Configuration

The application is configured via the use of environment variables.
See `src/quartz_api/cmd/server.conf` for the full specification of available environmental
configuration.

### Using Docker

Run the latest image from GitHub container registry:

```sh
$ docker run 
    -p 8000:8000 \
    -e <ENV_KEY>=<ENV_VALUE> \
    ghcr.io/openclimatefix/quartz-api:latest
```

## Development

Clone the repository. Install all the dependencies with

```
$ uv sync
```

### Running the service

To run the API locally, use the command

```
$ uv run quartz-api
```

The API should then be accessible at `http://localhost:8000`, and the docs at
`http://localhost:8000/docs` (or whatever port you have configured).


### Running Tests

Make sure that you have install the development dependencies (`uv sync` will do this for you).
Then run the tests using

```
uv run pytest
```

## Known Bugs

There may be some issues when installing this with windows.

## Contributors ✨

Thanks goes to these wonderful people ([emoji key](https://allcontributors.org/docs/en/emoji-key)):

<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->
<!-- prettier-ignore-start -->
<!-- markdownlint-disable -->
<table>
  <tbody>
    <tr>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/rahul-maurya11b"><img src="https://avatars.githubusercontent.com/u/98907006?v=4?s=100" width="100px;" alt="Rahul Maurya"/><br /><sub><b>Rahul Maurya</b></sub></a><br /><a href="https://github.com/openclimatefix/quartz-api/commits?author=rahul-maurya11b" title="Code">💻</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/DubraskaS"><img src="https://avatars.githubusercontent.com/u/87884444?v=4?s=100" width="100px;" alt="Dubraska Solórzano"/><br /><sub><b>Dubraska Solórzano</b></sub></a><br /><a href="https://github.com/openclimatefix/quartz-api/commits?author=DubraskaS" title="Code">💻</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/ProfessionalCaddie"><img src="https://avatars.githubusercontent.com/u/180212671?v=4?s=100" width="100px;" alt="Nicholas Tucker"/><br /><sub><b>Nicholas Tucker</b></sub></a><br /><a href="https://github.com/openclimatefix/quartz-api/commits?author=ProfessionalCaddie" title="Code">💻</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/devsjc"><img src="https://avatars.githubusercontent.com/u/47188100?v=4?s=100" width="100px;" alt="devsjc"/><br /><sub><b>devsjc</b></sub></a><br /><a href="https://github.com/openclimatefix/quartz-api/commits?author=devsjc" title="Code">💻</a></td>
      <td align="center" valign="top" width="14.28%"><a href="http://suvanbanerjee.github.io"><img src="https://avatars.githubusercontent.com/u/104707806?v=4?s=100" width="100px;" alt="Suvan Banerjee"/><br /><sub><b>Suvan Banerjee</b></sub></a><br /><a href="https://github.com/openclimatefix/quartz-api/commits?author=suvanbanerjee" title="Code">💻</a></td>
      <td align="center" valign="top" width="14.28%"><a href="http://anaskhan.me"><img src="https://avatars.githubusercontent.com/u/83116240?v=4?s=100" width="100px;" alt="Anas Khan"/><br /><sub><b>Anas Khan</b></sub></a><br /><a href="#infra-anxkhn" title="Infrastructure (Hosting, Build-Tools, etc)">🚇</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/Edwardcg17"><img src="https://avatars.githubusercontent.com/u/123040852?v=4?s=100" width="100px;" alt="Edwardcg17"/><br /><sub><b>Edwardcg17</b></sub></a><br /><a href="https://github.com/openclimatefix/quartz-api/commits?author=Edwardcg17" title="Code">💻</a></td>
    </tr>
    <tr>
      <td align="center" valign="top" width="14.28%"><a href="http://prashantpandeygit.github.io"><img src="https://avatars.githubusercontent.com/u/132379659?v=4?s=100" width="100px;" alt="Prashant Pandey"/><br /><sub><b>Prashant Pandey</b></sub></a><br /><a href="https://github.com/openclimatefix/quartz-api/commits?author=prashantpandeygit" title="Code">💻</a></td>
    </tr>
  </tbody>
</table>

<!-- markdownlint-restore -->
<!-- prettier-ignore-end -->

<!-- ALL-CONTRIBUTORS-LIST:END -->

This project follows the [all-contributors](https://github.com/all-contributors/all-contributors) specification. Contributions of any kind welcome!
