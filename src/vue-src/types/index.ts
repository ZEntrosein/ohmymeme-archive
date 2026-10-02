export interface Meme {
  id: number
  filename: string
  name: string
  file_hash: string
  compressed_only?: boolean
  from_stego?: number
  width: number
  height: number
  mime_type: string
  is_gif?: boolean
  is_animated?: boolean
  favorited?: boolean
  auto_play_gif?: boolean
  hover_to_play?: boolean
  genre?: 'image' | 'gif'
  category_id?: number | null
  collection_ids?: number[]
  recently_used?: boolean
}

export type Genre = 'image' | 'gif' | 'chat_record'

export interface ChatRecordSummary {
  id: number
  title: string
  name: string
  media_folder: string
  node_count: number
  record_count: number
  message_type: string
  peer_id: string
  source_message_id: string
  source_message_seq: string
  category_id?: number | null
  created_at: string
  updated_at: string
  genre: 'chat_record'
}

export interface Collection {
  id: number
  name: string
  count: number
  parent_id: number | null
  system_hidden?: boolean
  children?: Collection[]
}

export interface TagRecord {
  id?: number
  name: string
  category: string
}

export interface Category extends Collection {
  path?: string
}

export interface Page<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}
