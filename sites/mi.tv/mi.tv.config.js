const cheerio = require('cheerio')
const dayjs = require('dayjs')
const utc = require('dayjs/plugin/utc')
const customParseFormat = require('dayjs/plugin/customParseFormat')

dayjs.extend(utc)
dayjs.extend(customParseFormat)

const headers = {
  accept:
    'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
  'accept-language': 'en',
  'sec-fetch-site': 'same-origin',
  'sec-fetch-user': '?1',
  'upgrade-insecure-requests': '1',
  'User-Agent':
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36'
}

const MOVIE_GENRES = [
  'Acción',
  'Accion',
  'Animación',
  'Animacion',
  'Aventura',
  'Biografía',
  'Biografia',
  'Ciencia ficción',
  'Ciencia ficcion',
  'Comedia',
  'Crimen',
  'Documental',
  'Drama',
  'Familiar',
  'Fantasía',
  'Fantasia',
  'Historia',
  'Musical',
  'Misterio',
  'Romance',
  'Suspenso',
  'Terror',
  'Thriller',
  'Western'
].sort((a, b) => b.length - a.length)

module.exports = {
  site: 'mi.tv',
  days: 3,
  request: { headers },

  url({ date, channel }) {
    const [country, id] = channel.site_id.split('#')
    return `https://mi.tv/${country}/async/channel/${id}/${date.format('YYYY-MM-DD')}/0`
  },

  parser({ content, date }) {
    const programs = []
    const items = parseItems(content)

    items.forEach(item => {
      const prev = programs[programs.length - 1]
      const $item = cheerio.load(item)

      let start = parseStart($item, date)
      if (!start) return

      if (prev) {
        if (start.isBefore(prev.start)) {
          start = start.add(1, 'd')
          date = date.add(1, 'd')
        }
        prev.stop = start
      }

      const stop = start.add(1, 'h')
      const image = parseImage($item)
      const meta = parseProgramMeta($item, image)

      programs.push({
        ...meta,
        image,
        start,
        stop
      })
    })

    return programs
  },

  async channels({ country }) {
    let lang = 'es'
    if (country === 'br') lang = 'pt'

    const axios = require('axios')
    const data = await axios
      .get(`https://mi.tv/${country}/sitemap`)
      .then(r => r.data)
      .catch(console.log)

    const $ = cheerio.load(data)

    const channels = []
    $(`#page-contents a[href*="${country}/canales"], a[href*="${country}/canais"]`).each(
      (i, el) => {
        const name = $(el).text()
        const url = $(el).attr('href')
        const [, , , channelId] = url.split('/')

        channels.push({
          lang,
          name,
          site_id: `${country}#${channelId}`
        })
      }
    )

    return channels
  }
}

function normalizeText(text = '') {
  return text.replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim()
}

function normalizeYear(year) {
  if (!year) return null
  const match = String(year).match(/(19\d{2}|20\d{2})/)
  return match ? match[1] : null
}

function buildTitles(titleEs, titleEn = '') {
  const titles = []

  if (titleEs) {
    titles.push({ value: normalizeText(titleEs), lang: 'es' })
  }

  if (titleEn) {
    titles.push({ value: normalizeText(titleEn), lang: 'en' })
  }

  return titles
}

function parseStart($item, date) {
  const timeString = $item('a > div.content > span.time').text()
  if (!timeString) return null
  const dateString = `${date.format('MM/DD/YYYY')} ${timeString}`
  return dayjs.utc(dateString, 'MM/DD/YYYY HH:mm')
}

function parseTitle($item) {
  return normalizeText($item('a > div.content > h2').text())
}

function parseDescription($item) {
  return normalizeText($item('a > div.content > p.synopsis').text())
}

function parseHref($item) {
  const href = $item('a.program-link').attr('href')
  return href ? href.trim() : ''
}

function parseImage($item) {
  const styleAttr = $item('a > div.image-parent > div.image').attr('style')

  if (styleAttr) {
    const match = styleAttr.match(/background-image:\s*url\(['"]?(.*?)['"]?\)/)
    if (match) return cleanUrl(match[1])
  }

  const backgroundImage = $item('a > div.image-parent > div.image').css('background-image')
  if (backgroundImage && backgroundImage !== 'none') {
    const match = backgroundImage.match(/url\(['"]?(.*?)['"]?\)/)
    if (match) return cleanUrl(match[1])
  }

  return null
}

function cleanUrl(url) {
  if (!url) return null

  return url
    .replace(/^['"`\\]+/, '')
    .replace(/['"`\\]+$/, '')
    .replace(/\\'/g, "'")
    .replace(/\\"/g, '"')
    .replace(/\\\\/g, '\\')
}

function parseItems(content) {
  const $ = cheerio.load(content)
  return $('#listings > ul > li').toArray()
}

function parseSubtitleParts($item) {
  const nodes = $item('span.sub-title')
  if (!nodes.length) return []

  const parts = []

  nodes.each((_, node) => {
    const $node = $item(node)
    const html = $node.html() || ''

    if (/<br\s*\/?>/i.test(html)) {
      html
        .split(/<br\s*\/?>/i)
        .map(part => {
          const $part = cheerio.load(`<div>${part}</div>`)
          return normalizeText($part('div').text())
        })
        .filter(Boolean)
        .forEach(part => parts.push(part))

      return
    }

    const text = normalizeText($node.text())
    if (text) parts.push(text)
  })

  return parts
}

function isMovieItem($item) {
  return $item('a > div.content > h2 img.peli').length > 0
}

function looksLikeMovie(parts) {
  if (!parts.length) return false

  if (parts.length >= 2) {
    const metaText = normalizeText(parts.slice(1).join(' '))
    const hasYear = /(19\d{2}|20\d{2})/.test(metaText)
    const hasGenre = MOVIE_GENRES.some(genre =>
      metaText.toLowerCase().includes(genre.toLowerCase())
    )
    const hasRating = /(?:^|\/)\s*★?\s*[0-9]+(?:\.[0-9]+)?\s*$/.test(metaText)

    if (hasYear || hasGenre || hasRating) return true
  }

  const text = normalizeText(parts.join(' '))
  const hasYear = /(19\d{2}|20\d{2})/.test(text)
  const hasGenre = MOVIE_GENRES.some(genre =>
    text.toLowerCase().includes(genre.toLowerCase())
  )

  return hasYear && hasGenre
}

function parseProgramMeta($item, image) {
  const titleEs = parseTitle($item)
  const synopsis = parseDescription($item)
  const subtitleParts = parseSubtitleParts($item)
  const subtitleJoined = normalizeText(subtitleParts.join(' | '))
  const href = parseHref($item)

  const seriesFromText =
    extractSeriesInfoFromParts(subtitleParts) || extractSeriesInfoFromText(subtitleJoined)
  const seriesFromImage = extractSeriesInfoFromImage(image)
  const seriesFromHref = extractSeriesInfoFromHref(href)

  const isSeries = !!seriesFromText || !!seriesFromImage || !!seriesFromHref

  if (isSeries) {
    const season =
      seriesFromText?.season ??
      seriesFromImage?.season ??
      seriesFromHref?.season ??
      null

    const episode =
      seriesFromText?.episode ??
      seriesFromImage?.episode ??
      seriesFromHref?.episode ??
      null

    let episodeTitle = seriesFromText?.episode_title || ''
    if (!episodeTitle && seriesFromImage?.episode_slug) {
      episodeTitle = slugToTitle(seriesFromImage.episode_slug)
    }

    episodeTitle = normalizeText(episodeTitle)

    return {
      type: 'series',
      title: titleEs,
      titles: buildTitles(titleEs),
      title_es: titleEs,
      title_en: '',
      category: 'Serie',
      year: null,
      date: null,
      rating: null,
      description: synopsis,
      synopsis,
      season,
      episode,
      episode_title: episodeTitle,
      sub_title: episodeTitle,
      subtitle: episodeTitle,
      meta_line: subtitleJoined
    }
  }

  const movie = isMovieItem($item) || looksLikeMovie(subtitleParts)

  if (movie) {
    const movieData = extractMovieInfo(subtitleParts, image)

    return {
      type: 'movie',
      title: titleEs,
      titles: buildTitles(titleEs, movieData.title_en),
      title_es: titleEs,
      title_en: movieData.title_en,
      category: movieData.category,
      year: normalizeYear(movieData.year),
      date: null,
      rating: movieData.rating,
      description: synopsis,
      synopsis,
      season: null,
      episode: null,
      episode_title: '',
      sub_title: '',
      subtitle: '',
      meta_line: subtitleJoined
    }
  }

  return {
    type: 'show',
    title: titleEs,
    titles: buildTitles(titleEs),
    title_es: titleEs,
    title_en: '',
    category: subtitleParts[0] || '',
    year: null,
    date: null,
    rating: null,
    description: synopsis,
    synopsis,
    season: null,
    episode: null,
    episode_title: '',
    sub_title: '',
    subtitle: '',
    meta_line: subtitleJoined
  }
}

function extractSeriesInfoFromParts(parts) {
  for (const part of parts) {
    const info = extractSeriesInfoFromText(part)
    if (info) return info
  }

  return null
}

function extractSeriesInfoFromText(text) {
  if (!text) return null

  const normalized = normalizeText(text)

  const patterns = [
    /^Temporada\s*(\d+)\s*Episodio\s*(\d+)(?:\s*[-–—:]\s*(.+))?$/i,
    /^Temp\.?\s*(\d+)\s*Ep\.?\s*(\d+)(?:\s*[-–—:]\s*(.+))?$/i,
    /^S\s*(\d{1,2})\s*E\s*(\d{1,3})(?:\s*[-–—:]\s*(.+))?$/i,
    /^S(\d{1,2})E(\d{1,3})(?:\s*[-–—:]\s*(.+))?$/i
  ]

  for (const pattern of patterns) {
    const m = normalized.match(pattern)
    if (!m) continue

    let episodeTitle = normalizeText(m[3] || '')
    if (looksLikeScheduleNoise(episodeTitle)) {
      episodeTitle = ''
    }

    return {
      season: Number(m[1]),
      episode: Number(m[2]),
      episode_title: episodeTitle
    }
  }

  const episodeOnly = normalized.match(
    /^(?:Episodio|Ep\.?)\s*(\d{1,3})(?:\s*[-–—:]\s*(.+))?$/i
  )

  if (episodeOnly) {
    let episodeTitle = normalizeText(episodeOnly[2] || '')
    if (looksLikeScheduleNoise(episodeTitle)) {
      episodeTitle = ''
    }

    return {
      season: null,
      episode: Number(episodeOnly[1]),
      episode_title: episodeTitle
    }
  }

  return null
}

function extractSeriesInfoFromImage(imageUrl) {
  if (!imageUrl) return null

  const m = imageUrl.match(/-s(\d{1,2})e(\d{1,3})(?:-([a-z0-9-]+))?_/i)
  if (!m) return null

  return {
    season: Number(m[1]),
    episode: Number(m[2]),
    episode_slug: normalizeText((m[3] || '').replace(/-/g, ' '))
  }
}

function extractSeriesInfoFromHref(href) {
  if (!href) return null

  const m = href.match(/-s(\d{1,2})e(\d{1,3})(?:-[a-z0-9-]+)?$/i)
  if (!m) return null

  return {
    season: Number(m[1]),
    episode: Number(m[2])
  }
}

function looksLikeScheduleNoise(text) {
  if (!text) return false

  const normalized = normalizeText(text)
  return /^(\d{1,2}:\d{2}\s*(A\.M\.|P\.M\.)?)?(?:\s*#?\d+(\.\d+)?)?$/i.test(normalized)
}

function slugToTitle(slug) {
  if (!slug) return ''

  const text = normalizeText(slug.replace(/-/g, ' '))
  if (!text) return ''

  return text
    .split(' ')
    .map(word => {
      if (!word) return word
      return word.charAt(0).toUpperCase() + word.slice(1)
    })
    .join(' ')
}

function extractMovieInfo(parts, imageUrl) {
  if (!parts.length) {
    return {
      title_en: '',
      category: '',
      year: normalizeYear(extractYearFromImage(imageUrl)),
      rating: null
    }
  }

  if (parts.length >= 2) {
    const titleEn = normalizeText(parts[0])
    const meta = parseMovieMetaLine(parts.slice(1).join(' '))

    return {
      title_en: titleEn,
      category: meta.category,
      year: normalizeYear(meta.year || extractYearFromImage(imageUrl)),
      rating: meta.rating
    }
  }

  return splitCombinedMovieLine(parts[0], imageUrl)
}

function parseMovieMetaLine(line) {
  const text = normalizeText(line)
  const m = text.match(
    /^(.+?)\s*\/\s*(19\d{2}|20\d{2})(?:\s*\/\s*(?:★?\s*([0-9]+(?:\.[0-9]+)?))?)?$/i
  )

  if (!m) {
    return {
      category: text,
      year: null,
      rating: null
    }
  }

  return {
    category: normalizeText(m[1]),
    year: normalizeYear(m[2]),
    rating: m[3] || null
  }
}

function splitCombinedMovieLine(line, imageUrl) {
  const text = normalizeText(line)

  const yearMatch = text.match(
    /\/\s*(19\d{2}|20\d{2})(?:\s*\/\s*(?:★?\s*([0-9]+(?:\.[0-9]+)?))?)?$/i
  )
  const year = yearMatch
    ? normalizeYear(yearMatch[1])
    : normalizeYear(extractYearFromImage(imageUrl))
  const rating = yearMatch?.[2] || null

  let left = text
  if (yearMatch && typeof yearMatch.index === 'number') {
    left = normalizeText(text.slice(0, yearMatch.index))
  }

  const lower = left.toLowerCase()
  let genreIndex = -1

  for (const genre of MOVIE_GENRES) {
    const idx = lower.indexOf(genre.toLowerCase())
    if (idx > 0) {
      genreIndex = idx
      break
    }
  }

  if (genreIndex > 0) {
    return {
      title_en: normalizeText(left.slice(0, genreIndex)),
      category: normalizeText(left.slice(genreIndex)),
      year,
      rating
    }
  }

  return {
    title_en: left,
    category: '',
    year,
    rating
  }
}

function extractYearFromImage(imageUrl) {
  if (!imageUrl) return null

  const m = imageUrl.match(/-(19\d{2}|20\d{2})(?:-\d+)?_/i)
  return m ? m[1] : null
}
