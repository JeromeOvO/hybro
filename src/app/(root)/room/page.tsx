'use client'

import { useState, useEffect } from 'react'
import { Settings, Save } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { AgentSelector } from '@/components/agent-selector'
import { 
  createNewRoom, 
  inquiryRoomsByRoomOwnerId
} from '@/lib/api/room'
import type { Room } from '@/lib/types/room'
import type { Agent } from '@/lib/types/agent'

interface RoomFormData {
  roomName: string
  selectedAgents: { [agentId: string]: Agent }
}

export default function RoomPage() {
  const [formData, setFormData] = useState<RoomFormData>({
    roomName: '',
    selectedAgents: {}
  })
  const [rooms, setRooms] = useState<Room[]>([])
  const [loading, setLoading] = useState(false)
  const [isCreating, setIsCreating] = useState(false)

  // Mock user data - replace with actual user context
  const currentUser = {
    id: 'user_123',
    name: 'Current User'
  }

  // Load rooms on component mount
  useEffect(() => {
    loadRooms()
  }, [])

  const loadRooms = async () => {
    try {
      setLoading(true)
      const response = await inquiryRoomsByRoomOwnerId(currentUser.id)
      if (response.success && response.room_list) {
        setRooms(response.room_list)
      }
    } catch (error) {
      console.error('Failed to load rooms:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleAddAgent = (agent: Agent) => {
    setFormData(prev => ({
      ...prev,
      selectedAgents: {
        ...prev.selectedAgents,
        [agent.agent_id]: agent
      }
    }))
  }

  const handleRemoveAgent = (agentId: string) => {
    setFormData(prev => ({
      ...prev,
      selectedAgents: Object.fromEntries(
        Object.entries(prev.selectedAgents).filter(([id]) => id !== agentId)
      )
    }))
  }

  const handleCreateRoom = async () => {
    if (!formData.roomName.trim()) return

    try {
      setIsCreating(true)
      
      // Create agent set mapping
      const roomAgentSet = Object.fromEntries(
        Object.entries(formData.selectedAgents).map(([id, agent]) => [
          id, 
          agent.agent_card.name
        ])
      )

      const response = await createNewRoom(
        formData.roomName.trim(),
        currentUser.id,
        currentUser.name,
        roomAgentSet
      )
      
      if (response.success && response.room) {
        setRooms(prev => [...prev, response.room!])
        // Reset form
        setFormData({
          roomName: '',
          selectedAgents: {}
        })
      }
    } catch (error) {
      console.error('Failed to create room:', error)
    } finally {
      setIsCreating(false)
    }
  }

  return (
    <div className="container mx-auto py-6 space-y-6">
      {/* Create Room Form */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Settings className="w-5 h-5" />
            创建新房间
          </CardTitle>
          <CardDescription>
            设置房间名称并邀请代理加入您的聊天房间
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {/* Room Name Input */}
          <div className="space-y-2">
            <Label htmlFor="roomName">房间名称</Label>
            <Input
              id="roomName"
              placeholder="请输入房间名称"
              value={formData.roomName}
              onChange={(e) => setFormData(prev => ({ ...prev, roomName: e.target.value }))}
              className="max-w-md"
            />
          </div>

          <Separator />

          {/* Agent Management */}
          <AgentSelector
            selectedAgents={formData.selectedAgents}
            onAgentAdd={handleAddAgent}
            onAgentRemove={handleRemoveAgent}
          />

          <Separator />

          {/* Create Button */}
          <div className="flex justify-end">
            <Button 
              onClick={handleCreateRoom}
              disabled={!formData.roomName.trim() || isCreating}
              className="min-w-32"
            >
              <Save className="w-4 h-4 mr-2" />
              {isCreating ? '创建中...' : '创建房间'}
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Existing Rooms */}
      <Card>
        <CardHeader>
          <CardTitle>我的房间</CardTitle>
          <CardDescription>
            您创建的所有聊天房间
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="text-center py-8 text-muted-foreground">
              加载中...
            </div>
          ) : rooms.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              暂无房间，创建您的第一个房间吧！
            </div>
          ) : (
            <div className="grid gap-4">
              {rooms.map((room) => (
                <div
                  key={room.room_id}
                  className="flex items-center justify-between p-4 rounded-lg border hover:bg-muted/50 transition-colors"
                >
                  <div>
                    <div className="font-medium">{room.room_name}</div>
                    <div className="text-sm text-muted-foreground">
                      {room.room_agent_set 
                        ? `${Object.keys(room.room_agent_set).length} 个代理` 
                        : '无代理'
                      }
                      {room.room_created_at && (
                        <span className="ml-2">
                          创建于 {new Date(room.room_created_at).toLocaleDateString('zh-CN')}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        // Navigate to room detail or edit
                        console.log('Edit room:', room.room_id)
                      }}
                    >
                      管理
                    </Button>
                    <Button
                      size="sm"
                      onClick={() => {
                        // Navigate to chat room
                        window.location.href = `/room/${room.room_id}`
                      }}
                    >
                      进入
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
