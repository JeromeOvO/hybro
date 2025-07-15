import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

const recommendedItems = [
  { id: 1, name: 'Product A', description: 'A great product.' },
  { id: 2, name: 'Product B', description: 'Another fantastic choice.' },
  { id: 3, name: 'Product C', description: 'You might also like this.' },
];

export const Recommendations = () => {
  return (
    <div className="m-4">
      <h2 className="text-lg font-semibold mb-2">You May Also Like</h2>
      <div className="grid gap-4 md:grid-cols-3">
        {recommendedItems.map((item) => (
          <Card key={item.id}>
            <CardHeader>
              <CardTitle>{item.name}</CardTitle>
            </CardHeader>
            <CardContent>
              <p>{item.description}</p>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
};